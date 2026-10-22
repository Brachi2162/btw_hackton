"""Stage 8, decision (spec 9): candidates from every reader -> one
FieldResult per field, with a status, reason codes and a Hebrew explanation.

Confidence comes from agreement between independent sources and from
validation, never from the model's opinion of itself (iron rule 3):
- Barcodes support a VLM value when they encode exactly the same normalized
  value; the barcode box then anchors the value on the page.
- verified (green): checks pass, two independent source groups agree, and no
  close competitor. A barcode + VLM match is two groups; two reads by the
  same model are one.
  A field with its own check digit (`self_verifying`, D-007: the ID) needs
  one clear source only.
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
from vera.pipeline.gvision import find_value
from vera.profiles import GENERIC, Profile, load_profiles, pick
from vera.normalize import (
    LATIN_RE,
    Norm,
    SenderIndex,
    norm_central,
    norm_commitment,
    norm_date,
    norm_id,
    is_placeholder,
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
# Fields Vision OCR can confirm (D-009); the sender is a name from a list.
OCR_FIELDS = {
    "central_numbers",
    "commitment_numbers",
    "id_number",
    "receipt_date",
    "first_name",
    "last_name",
}


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
    self_verifying: set[str] = field(default_factory=set)
    label_required: set[str] = field(default_factory=set)
    rules_suffice: set[str] = field(default_factory=set)
    check_overrides_doubt: set[str] = field(default_factory=set)
    ocr_min_conf: float = 0.8
    hebrew_handwriting: set[str] = field(default_factory=set)
    profiles: list[Profile] = field(default_factory=list)

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
            self_verifying=set(c.get("self_verifying", [])),
            label_required=set(c.get("label_required", [])),
            rules_suffice=set(c.get("rules_suffice", [])),
            check_overrides_doubt=set(c.get("check_overrides_doubt", [])),
            ocr_min_conf=c.get("ocr_min_conf", 0.8),
            hebrew_handwriting=set(c.get("hebrew_handwriting", [])),
            profiles=load_profiles(config_dir),
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
    # The form type's pattern and place (a profile), instead of a label. It
    # says which field it is, not that every digit was read right.
    placed: bool = False

    @property
    def display(self) -> str:
        return self.value or self.raw

    def source_groups(self, cfg: DecisionConfig) -> set[str]:
        return {cfg.groups.get(c.reader, c.reader) for c in self.cands}


def _normalize(
    fname: str, raw: str, cfg: DecisionConfig, ref: date, profile=None
) -> Norm:
    if fname in ("first_name", "last_name"):
        return norm_name(raw)
    if fname == "id_number":
        return norm_id(raw)
    if fname == "central_numbers":
        return norm_central(raw)
    if fname == "commitment_numbers":
        if profile is not None and profile.commitment_regex:
            return norm_commitment(raw, profile.commitment_regex)
        return norm_commitment(raw)
    if fname == "receipt_date":
        return norm_date(raw, ref, cfg.max_age_days)
    if fname == "sender":
        return cfg.senders.norm(raw)
    return Norm(raw.strip() or None)


def _normalize_barcode(fname: str, text: str) -> Norm:
    return {
        "central_numbers": norm_central,
        "commitment_numbers": norm_commitment,
        "id_number": norm_id,
    }[fname](text)


def _barcode_value(fname: str, text: str) -> str | None:
    return _normalize_barcode(fname, text).value


def _support_with_barcodes(
    fname: str,
    cands: list[Candidate],
    barcodes: list[dict],
    page_id: str,
    anchors: list[str] = (),
) -> list[Candidate]:
    """A barcode becomes a candidate of a field when it encodes the same value
    a reader found for that field: the barcode alone can't tell which field
    it is (spec 3.6), the label next to the VLM value does. Or when the
    field's own label is printed right next to the barcode (D-016): then the
    barcode needs no reader at all."""
    out = []
    for b in barcodes:
        bv = _barcode_value(fname, b["text"])
        if bv is None:
            continue
        # The label decides the field only together with a valid value: a
        # neighbouring barcode inside the same label's reach fails the rule.
        valid = not _normalize_barcode(fname, b["text"]).problems
        if b.get("label") and _has_label(b["label"], anchors) and valid:
            out.append(
                Candidate(
                    field=fname,
                    value_raw=b["text"],
                    value_norm=bv,
                    reader="barcode",
                    page_id=page_id,
                    bbox=tuple(b["bbox"]),
                    evidence_text=f"{b['format']}: {b['text']} ({b['label']})",
                    label_nearby=b["label"],
                )
            )
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


def _support_with_ocr(
    fname: str,
    cands: list[Candidate],
    ocr: list[dict],
    page_id: str,
    cfg: DecisionConfig,
    ref: date,
    profile: Profile | None = None,
) -> list[Candidate]:
    """Vision found, in one line of the page, the same normalized value a
    reader chose (D-009): an independent source, with a real box. Like a
    barcode it proves the reading, not the field: labels and decoys still
    decide that. A low-confidence word doesn't count as a clear read."""
    out, seen = [], set()
    for c in cands:
        v = c.value_norm
        if v is None or v in seen or c.reader in ("barcode", "ocr"):
            continue
        seen.add(v)
        hit = find_value(
            ocr, v, lambda s: _normalize(fname, s, cfg, ref, profile).value
        )
        if hit is None:
            continue
        out.append(
            Candidate(
                field=fname,
                value_raw=hit.text,
                value_norm=v,
                reader="ocr",
                page_id=page_id,
                bbox=tuple(hit.bbox),
                evidence_text=f"Vision: {hit.line[:120]}",
                legible=hit.confidence >= cfg.ocr_min_conf,
                handwritten=c.handwritten,
                label_nearby=c.label_nearby,
                signals={"ocr_conf": hit.confidence},
            )
        )
    return out


def _in_profile_place(g: Group, fname: str, profile: Profile | None) -> bool:
    """The value has the form's own commitment pattern and sits where this
    form type prints it: the layout names the field, as a label would."""
    return (
        fname == "commitment_numbers"
        and profile is not None
        and bool(profile.commitment_regex and profile.commitment_regions)
        and not g.problems
        and any(c.signals.get("region") in profile.commitment_regions for c in g.cands)
    )


def _score(
    g: Group, fname: str, cfg: DecisionConfig, profile: Profile | None = None
) -> None:
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
    g.placed = not g.anchored and (
        _in_profile_place(g, fname, profile)
        # handwritten under the central number (D-020): the place says it is
        # the receipt date, not that every digit was read right
        or (
            fname == "receipt_date"
            and any(c.signals.get("by_central") for c in g.cands)
        )
    )
    if g.anchored or g.placed:
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
    # A crop read's box is the whole crop, not the value: still approximate.
    if not any(c.bbox and not c.bbox_approx for c in g.cands):
        reasons.append("bbox_approx")
    if fname in cfg.label_required and needs_anchor and not g.placed:
        reasons.append("label_missing")

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
        "label_missing",
    }
    # A strong check of its own (the ID check digit) outweighs the reader's
    # "not sure": a misread digit would have failed it (D-016).
    if fname in cfg.check_overrides_doubt:
        blocking = blocking - {"illegible"}
    # The field's label is next to it and every rule passes: that settles
    # which field it is, even when the models put it in different fields.
    if fname in cfg.rules_suffice and g.anchored:
        reasons = [r for r in reasons if r != "disagree_sources"]
    if blocking & set(reasons):
        return "review", reasons
    if fname in cfg.rules_suffice and not needs_anchor:
        return "verified", reasons  # all of the field's rules pass (D-016)
    # A profile's place stands in for the label only with two sources: the
    # pattern (a digit count) can't catch a misread digit (D-019).
    if len(srcs) >= 2 and not (needs_anchor and not g.placed):
        return "verified", reasons
    if fname in cfg.self_verifying:
        # The check digit already caught any single misread digit (D-007).
        return "verified", reasons
    return "likely", reasons


def _explain(cfg: DecisionConfig, status: str, reasons: list, ctx: dict) -> str:
    keys = [r for r in reasons if r not in ("bbox_approx",)]
    if status == "verified":
        keys = [
            (
                "verified_barcode"
                if ctx.get("barcode")
                else (
                    "verified_checksum"
                    if "source_single" in reasons
                    else "verified_sources"
                )
            )
        ]
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
        bbox_approx=c.bbox is None or c.bbox_approx,
        evidence_text=c.evidence_text,
        legible=c.legible,
        handwritten=c.handwritten,
        region=(
            c.signals.get("region")
            if isinstance(c.signals.get("region"), str)
            else None
        ),
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


MODEL_GROUPS = {"vlm", "vlm_escalated"}
HANDWRITING_FIELDS = {"first_name", "last_name", "id_number"}
CROP_GROUPS = {"vlm_crop", "vlm_crop_check"}


def _choose_multi(ranked: list[Group], cfg: DecisionConfig) -> list[Group]:
    """Which values belong to a multi-value field (central, commitment).

    Each model alone also picks up other numbers on the page (case, visit,
    the other field's number); taking the union adds both models' noise.
    So: valid, non-decoy values; of those, the ones both models agree on;
    if they agree on none, the stronger (escalation) model's values. This
    only selects values: green still needs two sources and the label."""
    pool = [g for g in ranked if not g.problems] or ranked
    pool = [g for g in pool if "decoy_proximity" not in g.reasons] or pool[:1]
    both = [g for g in pool if MODEL_GROUPS <= g.source_groups(cfg)]
    if both:
        return both
    stronger = [g for g in pool if "vlm_escalated" in g.source_groups(cfg)]
    if stronger:
        return stronger
    chosen = [g for g in pool if g.score > 0 or g.value is None] or pool[:1]
    # Several stickers: when some carry the field's label, unlabeled
    # numbers are more likely other numbers (case, commitment...).
    if any(g.anchored for g in chosen):
        chosen = [g for g in chosen if g.anchored]
    return chosen


def decide_field(
    fname: str,
    cands: list[Candidate],
    barcodes: list[dict],
    page_id: str,
    cfg: DecisionConfig,
    ref: date,
    aux_dates: dict[str, list[date]] | None = None,
    exclude: set[str] | None = None,
    ocr: list[dict] | None = None,
    profile: Profile | None = None,
) -> FieldResult:
    # "לא קריא", "unknown"... is a reader saying it couldn't read: not a value.
    unread = [c for c in cands if is_placeholder(c.value_raw)]
    cands = [c for c in cands if not is_placeholder(c.value_raw)]
    if unread and not cands:
        return FieldResult(
            field=fname,
            values=[],
            status="review",
            score=0.0,
            reasons=["illegible"],
            explanation_he=(
                "המקור ראה שכתוב משהו אבל לא הצליח לקרוא אותו; השדה נשאר ריק"
            ),
            sources=[_source(c) for c in unread],
        )
    for c in cands:
        n = _normalize(fname, c.value_raw, cfg, ref, profile)
        if (
            fname in cfg.hebrew_handwriting
            and c.handwritten
            and not n.problems
            and LATIN_RE.search(n.value or "")
        ):
            # Hebrew cursive looks Latin to a model ("Ireei" for ישראלי):
            # a handwritten name in Latin letters is a misread until checked.
            n.problems, n.rule = ["validation_failed"], "כתב יד עברי שנקרא כלטינית"
        c.value_norm = n.value
        c.signals["problems"] = ",".join(n.problems)
        c.signals["rule"] = n.rule
    if fname in BARCODE_FIELDS:
        cands = cands + _support_with_barcodes(
            fname, cands, barcodes, page_id, cfg.anchors.get(fname, [])
        )
    if ocr and fname in OCR_FIELDS:
        cands = cands + _support_with_ocr(fname, cands, ocr, page_id, cfg, ref, profile)

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
        _score(g, fname, cfg, profile)
    if fname in HANDWRITING_FIELDS:
        # A reading that passes the field's checks (the ID check digit) first;
        # then agreement between sources; then the crop read, made for
        # handwriting by the strongest model (D-005); then the score.
        ranked = sorted(
            groups.values(),
            key=lambda g: (
                not g.problems,
                len(g.source_groups(cfg) - {"barcode"}),
                bool(CROP_GROUPS & g.source_groups(cfg)),
                g.score,
            ),
            reverse=True,
        )
    else:
        ranked = sorted(groups.values(), key=lambda g: g.score, reverse=True)

    if fname in MULTI:
        chosen = _choose_multi(ranked, cfg)
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
    # A reading that every one of its readers marked unreadable is a guess:
    # it is offered to the secretary, never written as the value (iron rule
    # 1, D-013). The field stays empty and orange until a person picks.
    guesses = [g.display for g in chosen if "illegible" in g.reasons and g.display]
    values = [g.display for g in chosen if g.display not in guesses]
    if guesses:
        status = "review"
    return FieldResult(
        field=fname,
        values=values,
        status=status,
        score=round(sum(g.score for g in chosen) / len(chosen), 3),
        reasons=[r for r in reasons if r in ReasonCode.__args__],
        explanation_he=_explain(cfg, status, reasons, ctx)
        + ("; הניחוש מוצע לבחירה ולא נכתב" if guesses else ""),
        sources=[_source(c) for g in chosen for c in g.cands],
        alternatives=guesses + [g.display for g in rejected if g.display] + overridden,
    )


def _number_key(text: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", text.upper()).lstrip("0")


def _cross_field_conflicts(
    fields: dict[str, FieldResult],
    by_field: dict[str, list[Candidate]],
    cfg: DecisionConfig,
) -> None:
    """One number, two fields: when one model reads a number as the central
    number and another as the commitment (6-digit commitments exist, spec
    3.2), the sources disagree on the field and a human decides."""
    proposed: dict[str, set[str]] = defaultdict(set)
    for f in BARCODE_FIELDS:
        for c in by_field.get(f, []):
            if c.reader != "barcode":
                proposed[_number_key(c.value_raw)].add(f)
    for f in BARCODE_FIELDS:
        fr = fields[f]
        clash = [v for v in fr.values if proposed.get(_number_key(v), {f}) - {f}]
        # Green by its rules means its own label is next to it: that label,
        # not the models' vote, says which field it is (D-016).
        settled = fr.status == "verified" and f in cfg.rules_suffice
        if clash and not settled and fr.status in ("verified", "likely", "review"):
            fr.status = "review"
            fr.reasons = list(dict.fromkeys(fr.reasons + ["disagree_sources"]))
            others = sorted(proposed[_number_key(clash[0])] - {f})
            fr.explanation_he = (
                f"{clash[0]}: נקרא גם כ-{', '.join(others)}; "
                "המקורות לא מסכימים לאיזה שדה הוא שייך"
            )


def _name_split(first: FieldResult, last: FieldResult) -> None:
    """The prompt returns a one-line name in both fields when the order is
    unclear; the same value in both is a split we can't trust."""
    if first.values and first.values == last.values:
        for fr in (first, last):
            fr.status = "review"
            if "name_split_ambiguous" not in fr.reasons:
                fr.reasons.append("name_split_ambiguous")


def merge_reads(
    base: dict | None,
    escalated: dict | None,
    crop: dict | None = None,
    crop_check: dict | None = None,
) -> dict:
    """One page dict with the candidates of every model (each keeps its own
    `reader`, so they count as separate sources)."""
    merged = dict(base) if base and "error" not in base else None
    if escalated and "error" not in escalated:
        if merged is None:
            merged = {k: v for k, v in escalated.items() if k != "candidates"}
            merged["candidates"], merged["sender_guess"] = [], ""
        merged["candidates"] = list(merged.get("candidates", [])) + list(
            escalated.get("candidates", [])
        )
        merged["escalated_sender_guess"] = escalated.get("sender_guess", "")
        merged["escalated_model"] = escalated.get("model")
    if merged is None:
        return base or {"error": "missing"}
    for extra in (crop, crop_check):
        if extra and "error" not in extra:
            merged["candidates"] = list(merged["candidates"]) + list(
                extra.get("candidates", [])
            )
    return merged


# "947501-25.8": a central number with the receipt date written next to it,
# the usual way on screenshots (spec 3.1, 3.3).
_CENTRAL_WITH_DATE = re.compile(
    r"\d{6}\s*[-–]\s*(\d{1,2}\s*[./]\s*\d{1,2}(?:\s*[./]\s*\d{2,4})?)"
)


def _dates_next_to_centrals(cands: list[Candidate]) -> list[Candidate]:
    out = []
    for c in cands:
        m = _CENTRAL_WITH_DATE.search(c.value_raw)
        if m:
            out.append(
                c.model_copy(
                    update={
                        "field": "receipt_date",
                        "value_raw": m.group(1),
                        "value_norm": None,
                        "label_nearby": "ליד מרכזי",
                        "signals": dict(c.signals),
                    }
                )
            )
    return out


_ROWS3 = ("top", "middle", "bottom")
_COLS3 = ("left", "center", "right")


def _mark_by_central(
    cands: list[Candidate], barcodes: list[dict], cfg: DecisionConfig, page_size
) -> None:
    """Receipt date handwritten under the central number (organizers' spec):
    a handwritten date the reader placed in the same 3x3 cell as a barcode
    labelled as the central number, or the cell right below it."""
    if not page_size or not page_size[0]:
        return
    w, h = page_size
    anchors = cfg.anchors.get("central_numbers", [])
    near = set()
    for b in barcodes:
        if not (b.get("label") and _has_label(b["label"], anchors)):
            continue
        if _normalize_barcode("central_numbers", b["text"]).problems:
            continue
        x1, y1, x2, y2 = b["bbox"]
        row = min(2, int((y1 + y2) / 2 / h * 3))
        col = _COLS3[min(2, int((x1 + x2) / 2 / w * 3))]
        near.add(f"{_ROWS3[row]}-{col}")
        if row < 2:
            near.add(f"{_ROWS3[row + 1]}-{col}")
    for c in cands:
        if c.handwritten and c.signals.get("region") in near:
            c.signals["by_central"] = True


def decide_page(
    page_id: str,
    batch_id: str,
    vlm_page: dict,
    barcodes: list[dict],
    cfg: DecisionConfig,
    ref: date,
    ocr: list[dict] | None = None,
    page_size: tuple[int, int] | None = None,
) -> Submission:
    """One page = one submission (continuation pages are attached later).
    `page_size` (width, height) places values relative to the barcodes."""
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
            profile_id=GENERIC,
            page_kind="unknown",
            fields=fields,
            row_status="review",
        )

    by_field: dict[str, list[Candidate]] = defaultdict(list)
    for c in vlm_page.get("candidates", []):
        cand = Candidate.model_validate(c)
        by_field[cand.field].append(cand)
    for key, reader, model in (
        ("sender_guess", "vlm_full", vlm_page.get("model")),
        ("escalated_sender_guess", "vlm_escalated", vlm_page.get("escalated_model")),
    ):
        guess = vlm_page.get(key, "")
        if guess and guess != "unknown":
            by_field["sender"].append(
                Candidate(
                    field="sender",
                    value_raw=guess,
                    reader=reader,
                    model=model,
                    page_id=page_id,
                    evidence_text="sender_guess",
                )
            )
    by_field["receipt_date"] += _dates_next_to_centrals(
        [c for f in ("central_numbers", "id_number") for c in by_field.get(f, [])]
    )

    aux = {
        k: [d for c in by_field.get(k, []) if (d := parse_date(c.value_raw, ref))]
        for k in ("form_date", "sample_date")
    }

    fields: dict[str, FieldResult] = {}
    fields["sender"] = decide_field(
        "sender", by_field.get("sender", []), barcodes, page_id, cfg, ref, ocr=ocr
    )
    # The form type: the decided sender + page kind -> a profile (spec 4).
    page_kind: PageKind = vlm_page.get("page_kind", "unknown")
    sid = (fields["sender"].values or [None])[0]
    profile = pick(cfg.profiles, sid, page_kind)
    for f in ("central_numbers", "id_number", "first_name", "last_name"):
        fields[f] = decide_field(
            f, by_field.get(f, []), barcodes, page_id, cfg, ref, ocr=ocr
        )
    _mark_by_central(by_field.get("receipt_date", []), barcodes, cfg, page_size)
    fields["receipt_date"] = decide_field(
        "receipt_date",
        by_field.get("receipt_date", []),
        barcodes,
        page_id,
        cfg,
        ref,
        aux_dates=aux,
        ocr=ocr,
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
        ocr=ocr,
        profile=profile,
    )
    _cross_field_conflicts(fields, by_field, cfg)
    _name_split(fields["first_name"], fields["last_name"])
    sender = fields["sender"]
    if sender.values and sender.values[0] in cfg.senders.by_id:
        sid = sender.values[0]
        sender.values = [cfg.senders.name(sid)]
        # A parent organization (כללית) may hide a more specific hospital on
        # the form, and the most specific one is required (spec 3.5): on the
        # dev set a bare parent was always a hospital misread, so it's a check.
        if sender.status in ("verified", "likely") and cfg.senders.has_children(sid):
            sender.status = "review"
            sender.explanation_he = cfg.reasons_he.get("sender_parent_only", "")

    fields = {f: fields[f] for f in FIELDS}
    return Submission(
        id=f"{batch_id}:{page_id}",
        batch_id=batch_id,
        page_ids=[page_id],
        profile_id=profile.id if profile else GENERIC,
        page_kind=page_kind,
        fields=fields,
        row_status=worst_status([fr.status for fr in fields.values()]),
    )
