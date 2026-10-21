"""Accuracy report against the ground-truth table (spec 17).

Values are compared after the same normalization the engine uses; multi-value
fields are compared as sets. The key numbers (spec 17.3):
- final accuracy: correct fields / all fields, before any human correction;
- green accuracy: correct among `verified` (target 100%, Wilson upper bound);
- human touch rate: `likely` + `review` among all fields;
- correct empties: a field absent in the form stays empty.
"""

import json
import math
from collections import Counter, defaultdict
from datetime import date

from vera.models import Submission
from vera.normalize import (
    SenderIndex,
    norm_central,
    norm_commitment,
    norm_id,
    norm_name,
    parse_date,
)
from vera.pipeline.decide import FIELDS, MULTI


def _gt_values(field: str, text: str, senders: SenderIndex, ref: date) -> set[str]:
    parts = [p.strip() for p in (text or "").split(";") if p.strip()]
    out = set()
    for p in parts:
        if field in ("first_name", "last_name"):
            v = norm_name(p).value
        elif field == "id_number":
            v = norm_id(p).value
        elif field == "central_numbers":
            v = norm_central(p).value
        elif field == "commitment_numbers":
            v = norm_commitment(p).value
        elif field == "receipt_date":
            d = parse_date(p, ref)
            v = d.isoformat() if d else p
        elif field == "sender":
            sid = senders.match(p)
            v = senders.name(sid) if sid else p
        else:
            v = p
        out.add(v or p)
    return out


def _pred_values(field: str, values: list[str], senders: SenderIndex) -> set[str]:
    if field == "sender":
        out = set()
        for v in values:
            sid = senders.match(v)
            out.add(senders.name(sid) if sid else v)
        return out
    return set(values)


def wilson_upper(correct: int, n: int, z: float = 1.96) -> float:
    """Upper bound of the 95% Wilson interval for the error rate."""
    if n == 0:
        return 1.0
    p = (n - correct) / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return min(1.0, (centre + margin) / (1 + z * z / n))


def evaluate(
    subs: dict[str, Submission],
    gt_rows: list[dict],
    senders: SenderIndex,
    ref: date,
    split: str | None = None,
) -> dict:
    rows = [r for r in gt_rows if not split or r.get("split") == split]
    per_field = defaultdict(Counter)
    by_status = defaultdict(Counter)
    wrong_green, misses, draft = [], [], 0
    for r in rows:
        page_id = f"{r['doc']}/p{int(r['page']):02d}"
        draft += "טיוטה" in r.get("notes", "")
        sub = next((s for s in subs.values() if page_id in s.page_ids), None)
        if sub is None:
            continue
        for f in FIELDS:
            fr = sub.fields[f]
            truth = _gt_values(f, r.get(f, ""), senders, ref)
            pred = _pred_values(f, fr.values, senders)
            ok = truth == pred
            c = per_field[f]
            c["n"] += 1
            c["correct"] += ok
            c[fr.status] += 1
            c["empty_truth"] += not truth
            c["empty_correct"] += (not truth) and (not pred)
            by_status[fr.status]["n"] += 1
            by_status[fr.status]["correct"] += ok
            if not ok:
                item = {
                    "page": page_id,
                    "field": f,
                    "status": fr.status,
                    "predicted": sorted(pred),
                    "truth": sorted(truth),
                    "reasons": fr.reasons,
                }
                misses.append(item)
                if fr.status == "verified":
                    wrong_green.append(item)
            if f in MULTI and truth:
                c["values_truth"] += len(truth)
                c["values_found"] += len(truth & pred)

    total = sum(c["n"] for c in per_field.values())
    correct = sum(c["correct"] for c in per_field.values())
    green = by_status["verified"]
    touched = by_status["likely"]["n"] + by_status["review"]["n"]
    return {
        "split": split or "all",
        "rows": len(rows),
        "draft_rows": draft,
        "fields_total": total,
        "final_accuracy": correct / total if total else 0.0,
        "green_n": green["n"],
        "green_correct": green["correct"],
        "green_error_upper95": wilson_upper(green["correct"], green["n"]),
        "human_touch_rate": touched / total if total else 0.0,
        "by_status": {k: dict(v) for k, v in by_status.items()},
        "per_field": {k: dict(v) for k, v in per_field.items()},
        "wrong_green": wrong_green,
        "misses": misses,
    }


def to_markdown(rep: dict) -> str:
    pct = "{:.0%}".format
    lines = [
        f"# VERA eval · split={rep['split']} · {rep['rows']} עמודים",
        "",
    ]
    if rep["draft_rows"]:
        lines += [
            f"> ⚠️ {rep['draft_rows']} שורות בטבלת האמת עדיין מסומנות 'טיוטה' "
            "(לא אומתו). המספרים זמניים.",
            "",
        ]
    lines += [
        "| מדד | ערך |",
        "|---|---|",
        f"| דיוק סופי (לפני תיקון) | {pct(rep['final_accuracy'])} "
        f"({rep['fields_total']} שדות) |",
        f"| ירוקים | {rep['green_n']}, מתוכם שגויים "
        f"{rep['green_n'] - rep['green_correct']} "
        f"(גבול עליון לשגיאה 95%: {pct(rep['green_error_upper95'])}) |",
        f"| שיעור מגע אנושי (צהוב+כתום) | {pct(rep['human_touch_rate'])} |",
        "",
        "## לפי שדה",
        "",
        "| שדה | n | נכון | ירוק | צהוב | כתום | אפור | ריקים נכונים |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for f in FIELDS:
        c = rep["per_field"].get(f, {})
        n = c.get("n", 0)
        lines.append(
            f"| {f} | {n} | {pct(c.get('correct', 0) / n) if n else '-'} "
            f"| {c.get('verified', 0)} | {c.get('likely', 0)} | {c.get('review', 0)} "
            f"| {c.get('missing', 0)} "
            f"| {c.get('empty_correct', 0)}/{c.get('empty_truth', 0)} |"
        )
    lines += ["", "## לפי סטטוס", "", "| סטטוס | n | נכון |", "|---|---|---|"]
    for st in ("verified", "likely", "review", "missing", "human"):
        c = rep["by_status"].get(st)
        if c:
            lines.append(f"| {st} | {c['n']} | {pct(c['correct'] / c['n'])} |")
    if rep["wrong_green"]:
        lines += ["", "## ❌ ירוקים שגויים (חייב להיות 0)", ""]
        for m in rep["wrong_green"]:
            lines.append(
                f"- {m['page']} · {m['field']}: "
                f"נקרא {m['predicted']}, נכון {m['truth']}"
            )
    lines += ["", "## מה עדיין קשה (כל הטעויות)", ""]
    for m in rep["misses"]:
        lines.append(
            f"- {m['page']} · {m['field']} · {m['status']}: נקרא {m['predicted']}, "
            f"נכון {m['truth']} · {', '.join(m['reasons'][:3])}"
        )
    return "\n".join(lines) + "\n"


def dump(rep: dict) -> str:
    return json.dumps(rep, ensure_ascii=False, indent=2)
