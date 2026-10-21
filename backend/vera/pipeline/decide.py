"""Stage 8, decision (spec 9): candidates from every reader -> one
FieldResult per field, with a status, reason codes and a Hebrew explanation.

Confidence comes from agreement between independent sources and from
validation, never from the model's opinion of itself (iron rule 3):
- Barcodes support a VLM value when they encode exactly the same normalized
  value; the barcode box then anchors the value on the page.
- verified (green): checks pass, two independent source groups agree, and no
  close competitor. A barcode + VLM match is two groups; two reads by the
  same model are one.
- likely (yellow): checks pass, one source group only.
- review (orange): failed check, illegible, decoy label, competing values,
  date conflicts, unreadable page.
- missing (grey): nothing found.
Weights, labels and thresholds live in config/confidence.yaml.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from vera.models import (
    Candidate,
    FieldResult,
    PageKind,
    ReasonCode,
    Source,
    Status,
    Submission,
    worst_status,
)
from vera.normalize import (
    Norm,
    SenderIndex,
    norm_central,
    norm_commitment,
    norm_date,
    norm_id,
    norm_name,
    parse_date,
)

# Exported fields, in spec 6.2 order.
FIELDS = [
    "first_name",
    "last_name",
    "id_number",
    "central_numbers",
    "receipt_date",
    "sender",
    "commitment_numbers",
]
MULTI = {"central_numbers", "commitment_numbers"}
BARCODE_FIELDS = {"central_numbers", "commitment_numbers", "id_number"}


@dataclass
class DecisionConfig:
    weights: dict[str, float]
    groups: dict[str, str]
    anchor_bonus: float
    illegible_penalty: float
    decoy_penalty: float
    competitor_margin: float
    anchors: dict[str, list[str]]
    decoys: dict[str, list[str]]
    max_age_days: int
    max_days_after_form: int
    reasons_he: dict[str, str]
    senders: SenderIndex
    commitment_aliases: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, config_dir: Path) -> "DecisionConfig":
        def y(name):
            return yaml.safe_load((config_dir / name).read_text(encoding="utf-8"))

        c, vlm = y("confidence.yaml"), y("vlm.yaml")
        anchors = dict(c["anchors"])
        anchors.setdefault("commitment_numbers", vlm.get("commitment_aliases", []))
        return cls(
            weights=c["source_weights"],
            groups=c["source_groups"],
            anchor_bonus=c["anchor_bonus"],
            illegible_penalty=c["illegible_penalty"],
            decoy_penalty=c["decoy_penalty"],
            competitor_margin=c["competitor_margin"],
            anchors=anchors,
            decoys=c["decoys"],
            max_age_days=c["dates"]["max_age_days"],
            max_days_after_form=c["dates"]["max_days_after_form"],
            reasons_he=y("reasons.he.yaml"),
            senders=SenderIndex(y("senders.yaml")),
            commitment_aliases=vlm.get("commitment_aliases", []),
        )


def _key(text: str) -> str:
    return re.sub(r"[\s\"'״׳.:\-()]", "", text or "").lower()


def _has_label(label: str | None, words: list[str]) -> str | None:
    k = _key(label or "")
    if not k:
        return None
    return next((w for w in words if _key(w) and _key(w) in k), None)


@dataclass
class Group:
    """All candidates that normalize to the same value of one field."""

    value: str | None  # normalized; None = could not normalize
    raw: str
    cands: list[Candidate] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    rule: str = ""
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    decoy_label: str | None = None
    anchored: bool = False  # the field's own label was next to the value

    @property
    def display(self) -> str:
        return self.value or self.raw

    def source_groups(self, cfg: DecisionConfig) -> set[str]:
        return {cfg.groups.get(c.reader, c.reader) for c in self.cands}


def _normalize(fname: str, raw: str, cfg: DecisionConfig, ref: date) -> Norm:
    if fname in ("first_name", "last_name"):
        return norm_name(raw)
    if fname == "id_number":
        return norm_id(raw)
    if fname == "central_numbers":
        return norm_central(raw)
    if fname == "commitment_numbers":
        return norm_commitment(raw)
    if fname == "receipt_date":
        return norm_date(raw, ref, cfg.max_age_days)
    if fname == "sender":
        return cfg.senders.norm(raw)
    return Norm(raw.strip() or None)


def _barcode_value(fname: str, text: str) -> str | None:
    n = {
        "central_numbers": norm_central,
        "commitment_numbers": norm_commitment,
        "id_number": norm_id,
    }[fname](text)
    return n.value


def _support_with_barcodes(
    fname: str, cands: list[Candidate], barcodes: list[dict], page_id: str
) -> list[Candidate]:
    """A barcode becomes a candidate of a field only when it encodes the same
    value a reader found for that field: the barcode alone can't tell which
    field it is (spec 3.6), the label next to the VLM value does."""
    out = []
    for b in barcodes:
        bv = _barcode_value(fname, b["text"])
        if bv is None:
            continue
        for c in cands:
            if c.value_norm == bv:
                out.append(
                    Candidate(
                        field=fname,
                        value_raw=b["text"],
                        value_norm=bv,
                        reader="barcode",
                        page_id=page_id,
                        bbox=tuple(b["bbox"]),
                        evidence_text=f"{b['format']}: {b['text']}",
                        label_nearby=c.label_nearby,
                    )
                )
                break
    return out


def _score(g: Group, fname: str, cfg: DecisionConfig) -> None:
    per_group: dict[str, list[float]] = defaultdict(list)
    for c in g.cands:
        per_group[cfg.groups.get(c.reader, c.reader)].append(
            cfg.weights.get(c.reader, 0.5)
        )
    # Within one source group: the best read, plus extra reads (vlm_crop).
    g.score = sum(min(sum(ws), max(ws) + 0.4) for ws in per_group.values())

    g.anchored = any(
        _has_label(c.label_nearby, cfg.anchors.get(fname, [])) for c in g.cands
    )
    if g.anchored:
        g.score += cfg.anchor_bonus
    decoy = next(
        (
            hit
            for c in g.cands
            if (hit := _has_label(c.label_nearby, cfg.decoys.get(fname, [])))
        ),
        None,
    )
    # A barcode proves the digits, not the field: decoys apply to it too.
    if decoy:
        g.decoy_label = decoy
        g.score -= cfg.decoy_penalty
        g.reasons.append("decoy_proximity")
    if all(not c.legible for c in g.cands):
        g.score -= cfg.illegible_penalty
        g.reasons.append("illegible")
    if g.problems:
        g.score -= 1.0
        g.reasons += g.problems


def _status(
    g: Group, fname: str, cfg: DecisionConfig, competitor: bool
) -> tuple[Status, list]:
    reasons = list(dict.fromkeys(g.reasons))
    srcs = g.source_groups(cfg)
    # Agreement proves the value was read right, not that it belongs to this
    # field: green also needs the field's label next to it (spec 9.3).
    needs_anchor = bool(cfg.anchors.get(fname)) and not g.anchored
    if competitor:
        reasons.append("multiple_candidates")
    if len(srcs) < 2:
        only_vlm = srcs <= {"vlm"}
        reasons.append("source_single")
        if only_vlm and len({c.reader for c in g.cands}) > 1:
            reasons.append("sources_dependent")
        if all(c.handwritten for c in g.cands if c.reader != "barcode"):
            reasons.append("handwritten_only")
    if not any(c.bbox for c in g.cands):
        reasons.append("bbox_approx")

    blocking = {
        "validation_failed",
        "checksum_failed",
        "date_out_of_range",
        "date_year_anomaly",
        "date_conflict",
        "decoy_proximity",
        "illegible",
        "multiple_candidates",
        "name_split_ambiguous",
    }
    if blocking & set(reasons):
        return "review", reasons
    if len(srcs) >= 2 and not needs_anchor:
        return "verified", reasons
    return "likely", reasons


def _explain(cfg: DecisionConfig, status: str, reasons: list, ctx: dict) -> str:
    keys = [r for r in reasons if r not in ("bbox_approx",)]
    if status == "verified":
        keys = ["verified_barcode" if ctx.get("barcode") else "verified_sources"]
    elif status == "missing":
        keys = keys or ["missing"]
    texts = []
    for k in keys[:2]:
        tpl = cfg.reasons_he.get(k)
        if tpl:
            texts.append(re.sub(r"\{(\w+)\}", lambda m: str(ctx.get(m[1], "?")), tpl))
    return "; ".join(texts)


def _source(c: Candidate) -> Source:
    return Source(
        reader=c.reader,
        model=c.model,
        values=[c.value_norm or c.value_raw],
        page_id=c.page_id,
        bbox=c.bbox,
        bbox_approx=c.bbox is None,
        evidence_text=c.evidence_text,
        legible=c.legible,
        handwritten=c.handwritten,
    )


def _date_checks(
    groups: list[Group], aux: dict[str, list[date]], cfg: DecisionConfig
) -> None:
    """Receipt date against the form/sample dates on the same page (spec 3.3)."""
    others = [d for k in ("form_date", "sample_date") for d in aux.get(k, [])]
    for g in groups:
        if g.value is None:
            continue
        d = date.fromisoformat(g.value)
        if others:
            latest = max(others)
            if d < latest or (d - latest).days > cfg.max_days_after_form:
                g.problems.append("date_conflict")
                g.rule = latest.strftime("%d/%m/%Y")
            years = {o.year for o in others}
            if len(years) == 1 and d.year not in years:
                g.problems.append("date_year_anomaly")


def decide_field(
    fname: str,
    cands: list[Candidate],
    barcodes: list[dict],
    page_id: str,
    cfg: DecisionConfig,
    ref: date,
    aux_dates: dict[str, list[date]] | None = None,
    exclude: set[str] | None = None,
) -> FieldResult:
    for c in cands:
        n = _normalize(fname, c.value_raw, cfg, ref)
        c.value_norm = n.value
        c.signals["problems"] = ",".join(n.problems)
        c.signals["rule"] = n.rule
    if fname in BARCODE_FIELDS:
        cands = cands + _support_with_barcodes(fname, cands, barcodes, page_id)

    overridden = []
    if fname in ("first_name", "last_name") and any(c.handwritten for c in cands):
        # Handwriting over a printed name is a correction and wins (spec 3.4).
        overridden = [c.value_norm or c.value_raw for c in cands if not c.handwritten]
        cands = [c for c in cands if c.handwritten]

    groups: dict[str, Group] = {}
    for c in cands:
        k = c.value_norm if c.value_norm is not None else f"raw:{c.value_raw}"
        g = groups.setdefault(k, Group(c.value_norm, c.value_raw))
        g.cands.append(c)
        if c.reader != "barcode" and c.signals.get("problems"):
            g.problems = sorted(set(g.problems) | set(c.signals["problems"].split(",")))
            g.rule = str(c.signals.get("rule", ""))
    if fname == "receipt_date":
        _date_checks(list(groups.values()), aux_dates or {}, cfg)

    excluded = []
    for k in list(groups):
        if exclude and groups[k].value in exclude:
            excluded.append(groups.pop(k))  # e.g. a commitment equal to a central
    for g in groups.values():
        _score(g, fname, cfg)
    ranked = sorted(groups.values(), key=lambda g: g.score, reverse=True)

    if fname in MULTI:
        chosen = [g for g in ranked if g.score > 0 or g.value is None]
        chosen = [g for g in chosen if "decoy_proximity" not in g.reasons] or chosen[:1]
        # Several stickers: when some carry the field's label, unlabeled
        # numbers are more likely other numbers (case, commitment...).
        if any(g.anchored for g in chosen):
            chosen = [g for g in chosen if g.anchored]
    else:
        chosen = ranked[:1]
    rejected = [g for g in ranked if g not in chosen] + excluded

    if not chosen:
        return FieldResult(
            field=fname,
            values=[],
            status="missing",
            score=0.0,
            reasons=[],
            explanation_he=_explain(cfg, "missing", [], {}),
            alternatives=[g.display for g in rejected],
        )

    competitor = (
        fname not in MULTI
        and len(ranked) > 1
        and ranked[0].score - ranked[1].score < cfg.competitor_margin
    )
    statuses, reasons = [], []
    for g in chosen:
        st, rs = _status(g, fname, cfg, competitor)
        statuses.append(st)
        reasons += rs
    reasons = list(dict.fromkeys(reasons))
    status = worst_status(statuses)
    top = chosen[0]
    ctx = {
        "value": top.display,
        "values": ", ".join(g.display for g in ranked[:3]),
        "n": len(ranked),
        "rule": top.rule,
        "label": top.decoy_label,
        "reader": "+".join(sorted({c.reader for c in top.cands})),
        "barcode": any(c.reader == "barcode" for c in top.cands),
        "other": top.rule,
        "y1": top.value[:4] if top.value and fname == "receipt_date" else "?",
        "y2": ", ".join(
            sorted({str(d.year) for v in (aux_dates or {}).values() for d in v})
        ),
    }
    return FieldResult(
        field=fname,
        values=[g.display for g in chosen],
        status=status,
        score=round(sum(g.score for g in chosen) / len(chosen), 3),
        reasons=[r for r in reasons if r in ReasonCode.__args__],
        explanation_he=_explain(cfg, status, reasons, ctx),
        sources=[_source(c) for g in chosen for c in g.cands],
        alternatives=[g.display for g in rejected if g.display] + overridden,
    )


def _name_split(first: FieldResult, last: FieldResult) -> None:
    """The prompt returns a one-line name in both fields when the order is
    unclear; the same value in both is a split we can't trust."""
    if first.values and first.values == last.values:
        for fr in (first, last):
            fr.status = "review"
            if "name_split_ambiguous" not in fr.reasons:
                fr.reasons.append("name_split_ambiguous")


def decide_page(
    page_id: str,
    batch_id: str,
    vlm_page: dict,
    barcodes: list[dict],
    cfg: DecisionConfig,
    ref: date,
) -> Submission:
    """One page = one submission (continuation pages are attached later)."""
    if "error" in vlm_page:
        fields = {
            f: FieldResult(
                field=f,
                values=[],
                status="review",
                score=0.0,
                reasons=["vlm_invalid"],
                explanation_he=cfg.reasons_he.get("vlm_invalid", ""),
            )
            for f in FIELDS
        }
        return Submission(
            id=f"{batch_id}:{page_id}",
            batch_id=batch_id,
            page_ids=[page_id],
            profile_id="_generic",
            page_kind="unknown",
            fields=fields,
            row_status="review",
        )

    by_field: dict[str, list[Candidate]] = defaultdict(list)
    for c in vlm_page.get("candidates", []):
        cand = Candidate.model_validate(c)
        by_field[cand.field].append(cand)
    guess = vlm_page.get("sender_guess", "")
    if guess and guess != "unknown":
        by_field["sender"].append(
            Candidate(
                field="sender",
                value_raw=guess,
                reader="vlm_full",
                model=vlm_page.get("model"),
                page_id=page_id,
                evidence_text="sender_guess",
            )
        )

    aux = {
        k: [d for c in by_field.get(k, []) if (d := parse_date(c.value_raw, ref))]
        for k in ("form_date", "sample_date")
    }

    fields: dict[str, FieldResult] = {}
    for f in ("central_numbers", "id_number", "first_name", "last_name", "sender"):
        fields[f] = decide_field(f, by_field.get(f, []), barcodes, page_id, cfg, ref)
    fields["receipt_date"] = decide_field(
        "receipt_date",
        by_field.get("receipt_date", []),
        barcodes,
        page_id,
        cfg,
        ref,
        aux_dates=aux,
    )
    # A commitment number is never the central number or the ID (spec 5).
    taken = set(fields["central_numbers"].values) | set(fields["id_number"].values)
    fields["commitment_numbers"] = decide_field(
        "commitment_numbers",
        by_field.get("commitment_numbers", []),
        barcodes,
        page_id,
        cfg,
        ref,
        exclude=taken,
    )
    _name_split(fields["first_name"], fields["last_name"])
    if fields["sender"].values:
        sid = fields["sender"].values[0]
        if sid in cfg.senders.by_id:
            fields["sender"].values = [cfg.senders.name(sid)]

    fields = {f: fields[f] for f in FIELDS}
    page_kind: PageKind = vlm_page.get("page_kind", "unknown")
    return Submission(
        id=f"{batch_id}:{page_id}",
        batch_id=batch_id,
        page_ids=[page_id],
        profile_id="_generic",
        page_kind=page_kind,
        fields=fields,
        row_status=worst_status([fr.status for fr in fields.values()]),
    )
