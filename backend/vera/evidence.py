"""The "why?" of one field: what each source read, which checks passed, and
the decision in plain Hebrew. Built only from the stored FieldResult, so it
explains exactly what the table shows."""

import re

from vera.models import FieldResult, Submission

READER_HE = {
    "barcode": "ברקוד",
    "ocr": "OCR",
    "vlm_full": "מודל ראייה",
    "vlm_crop": "מודל ראייה (חיתוך)",
    "vlm_crop_check": "מודל ראייה שני (חיתוך)",
    "vlm_escalated": "מודל ראייה חזק",
    "human": "ידני",
}

FIELD_HE = {
    "first_name": "שם פרטי",
    "last_name": "שם משפחה",
    "id_number": "ת.ז.",
    "central_numbers": "מספר מרכזי",
    "commitment_numbers": "מספר התחייבות",
    "receipt_date": "תאריך קבלה",
    "sender": "גורם שולח",
}

STATUS_HE = {
    "verified": "מאומת",
    "likely": "סביר",
    "review": "לבדיקה",
    "missing": "חסר",
    "human": "אושר על ידי אדם",
}

# (label, field filter or None, reason codes that fail it)
CHECKS = [
    ("שני מקורות בלתי תלויים", None, {"source_single", "sources_dependent"}),
    ("המקורות מסכימים", None, {"disagree_sources", "multiple_candidates"}),
    ("עומד בכללי השדה", None, {"validation_failed", "vlm_invalid"}),
    ("ספרת ביקורת תקינה", "id_number", {"checksum_failed"}),
    ("תאריך בטווח הסביר", "receipt_date", {"date_out_of_range"}),
    (
        "מתאים לתאריכי הטופס",
        "receipt_date",
        {"date_conflict", "date_year_anomaly"},
    ),
    ("מתאים לרצף המספרים", "central_numbers", {"sequence_outlier"}),
    ("נמצא ליד התווית הנכונה", None, {"decoy_proximity"}),
    ("קריא", None, {"illegible", "handwritten_only"}),
    ("לא מופיע בטופס אחר באצווה", None, {"duplicate_in_batch"}),
]

PAD = 24


def _approx_area(fr: FieldResult, pages: dict) -> dict | None:
    """No exact box: where the readers said the value is. Each rough area (a
    crop read's crop, a full-page read's 3x3 region) contains the value, so
    their intersection is the tightest honest guess; if they don't overlap,
    the smallest one. Marked approx, never zoomed into as if exact (D-015)."""
    from vera.pipeline.vlm import region_box

    areas: dict[str, list] = {}
    for s in fr.sources:
        if s.bbox is not None:
            areas.setdefault(s.page_id, []).append(list(s.bbox))
        elif s.region and s.page_id in pages:
            w, h = pages[s.page_id]
            areas.setdefault(s.page_id, []).append(list(region_box(s.region, w, h)))
    if not areas:
        return None
    page, boxes = max(areas.items(), key=lambda kv: len(kv[1]))
    x1, y1 = max(b[0] for b in boxes), max(b[1] for b in boxes)
    x2, y2 = min(b[2] for b in boxes), min(b[3] for b in boxes)
    if x2 - x1 < 40 or y2 - y1 < 40:  # they disagree: the smallest area
        x1, y1, x2, y2 = min(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    return {"page_id": page, "bbox": [x1, y1, x2, y2], "approx": True}


def _crop(fr: FieldResult, pages: dict | None = None) -> dict | None:
    """Where to look on the page. Exact boxes (barcode, OCR, a place a person
    marked) when there are any; otherwise the readers' rough area, marked
    approx so the screen outlines it instead of zooming into its middle.
    `pages`: page_id -> (width, height), to place a 3x3 region."""
    boxes = [s for s in fr.sources if s.bbox is not None]
    # Rows saved before the fix carry crop boxes marked exact: by reader too.
    exact = [s for s in boxes if not s.bbox_approx and s.reader != "vlm_crop"]
    # A place a person marked beats every reader (D-011).
    marked = [s for s in exact if s.reader == "human"]
    if not (marked or exact):
        return _approx_area(fr, pages or {})
    boxes = marked or exact
    page = boxes[0].page_id
    same = [s.bbox for s in boxes if s.page_id == page]
    x1 = min(b[0] for b in same) - PAD
    y1 = min(b[1] for b in same) - PAD
    x2 = max(b[2] for b in same) + PAD
    y2 = max(b[3] for b in same) + PAD
    return {
        "page_id": page,
        "bbox": [max(0, x1), max(0, y1), x2, y2],
        "approx": False,
    }


def options(fr: FieldResult, learned: list[dict] | None = None) -> list[dict]:
    """Distinct candidate values with who read them: the 1/2/3 buttons. A fix
    people made to this same misread before comes first (D-011)."""
    out: dict[str, set[str]] = {}
    for x in learned or []:
        out.setdefault(x["value"], set()).add(f"תוקן כך {x['count']} פעמים בעבר")
    for s in fr.sources:
        for v in s.values:
            if v:
                out.setdefault(v, set()).add(READER_HE.get(s.reader, s.reader))
    for v in fr.values:
        out.setdefault(v, set())
    unread = "illegible" in fr.reasons and not fr.values
    for v in fr.alternatives:
        # With an empty unreadable field, the alternatives start with the
        # readers' guess (D-013): say so, not "rejected".
        out.setdefault(v, set()).add("ניחוש, לא קריא" if unread else "מועמד שנדחה")
    return [{"value": v, "readers": sorted(r)} for v, r in out.items()][:5]


def _reason_text(templates: dict[str, str], code: str, fr: FieldResult) -> str:
    tpl = templates.get(code, code)
    value = ", ".join(fr.values) or "?"
    return re.sub(r"\{(\w+)\}", lambda m: value if m[1] == "value" else "?", tpl)


def decision_he(fr: FieldResult) -> str:
    label = STATUS_HE.get(fr.status, fr.status)
    if fr.status == "human" and fr.history:
        last = fr.history[-1]
        when = last.at.strftime("%d/%m %H:%M")
        what = "אישר/ה" if last.old_values == last.new_values else "תיקן/ה"
        return f"{label}: {last.user} {what} את הערך ב-{when}. {fr.explanation_he}"
    body = fr.explanation_he or ""
    if fr.status == "verified":
        return f"{label}: {body}. אין צורך בבדיקה."
    if fr.status == "likely":
        return f"{label}: {body}. הערך כנראה נכון, אבל לא אומת משני מקורות."
    if fr.status == "review":
        return f"{label}: {body}. המערכת לא מנחשת. צריך עין אנושית."
    return f"{label}: {body or 'לא נמצא בעמוד'}. השדה נשאר ריק ומסומן."


def evidence(
    sub: Submission,
    field: str,
    templates: dict[str, str],
    learned: list[dict] | None = None,
    pages: dict | None = None,
) -> dict:
    fr = sub.fields[field]
    crop = _crop(fr, pages)
    reasons = set(fr.reasons)
    checks = []
    if fr.status == "missing" and not fr.sources:
        checks.append({"label": "נמצא בעמוד", "ok": False})
    else:
        for label, only, failing in CHECKS:
            if only and only != field:
                continue
            hit = sorted(reasons & failing)
            checks.append(
                {
                    "label": label,
                    "ok": not hit,
                    "detail": "; ".join(_reason_text(templates, c, fr) for c in hit),
                }
            )
    return {
        "submission_id": sub.id,
        "field": field,
        "field_he": FIELD_HE.get(field, field),
        "status": fr.status,
        "values": fr.values,
        "crop": crop,
        "sources": [
            {
                "reader": s.reader,
                "reader_he": READER_HE.get(s.reader, s.reader),
                "model": s.model,
                "values": s.values,
                "quote": s.evidence_text,
                "page_id": s.page_id,
                "bbox": s.bbox,
                "legible": s.legible,
                "handwritten": s.handwritten,
            }
            for s in fr.sources
        ],
        "options": options(fr, learned),
        "checks": checks,
        "reasons": [
            {"code": c, "text_he": _reason_text(templates, c, fr)} for c in fr.reasons
        ],
        "decision_he": decision_he(fr),
        "history": [h.model_dump(mode="json") for h in fr.history],
    }
