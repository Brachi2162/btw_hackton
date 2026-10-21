"""Weekly report: time saved, quality by sender, tubes without a form.

Hours saved = forms x MANUAL_SECONDS_PER_FORM, minus REVIEW_SECONDS_PER_FIELD
for every field a person still looked at. A field "needs an eye" when the
system did not verify it on its own (likely, review, missing, or corrected).
Every chart comes with a one-line takeaway, written here so the UI and the
assistant say the same thing.
"""

import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone

from sqlmodel import select

from vera.db import BatchRow, CorrectionRow, FollowupRow, TubeScanRow
from vera.followups import LAB, aware
from vera.views import value

DAYS_HE = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
UNKNOWN = "לא ידוע"


def _range(end: date | None) -> tuple[datetime, datetime, list[date]]:
    end = end or datetime.now(timezone.utc).date()
    start = end - timedelta(days=6)
    t0 = datetime.combine(start, time.min, tzinfo=timezone.utc)
    t1 = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return t0, t1, [start + timedelta(days=i) for i in range(7)]


def _generic(templates: dict[str, str], code: str) -> str:
    """A reason's Hebrew text without the per-field placeholders."""
    t = templates.get(code, code)
    t = re.sub(r"\s*\([^)]*\{\w+\}[^)]*\)", "", t)
    t = re.sub(r":?\s*\{\w+\}", "", t)
    return t.strip(" :")


def _forms(bs, t0, t1, demo: bool):
    with bs.sv.repo.session() as s:
        batches = s.exec(select(BatchRow)).all()
    chosen = [
        b for b in batches if t0 <= aware(b.created_at) < t1 and (demo or not b.demo)
    ]
    subs = [sub for b in chosen for sub in bs.submissions(b.id)]
    return chosen, subs


def weekly(bs, end: date | None = None, demo: bool = True) -> dict:
    s_ = bs.s
    t0, t1, days = _range(end)
    batches, subs = _forms(bs, t0, t1, demo)
    fields_total = sum(len(x.fields) for x in subs)
    eye = [
        (x, f, fr)
        for x in subs
        for f, fr in x.fields.items()
        if fr.status != "verified"
    ]
    auto = sum(1 for x in subs if x.row_status == "verified")
    saved_s = len(subs) * s_.manual_seconds_per_form - len(eye) * (
        s_.review_seconds_per_field
    )

    with bs.sv.repo.session() as s:
        scans = [
            r
            for r in s.exec(select(TubeScanRow)).all()
            if t0 <= aware(r.scanned_at) < t1 and (demo or not r.demo)
        ]
        open_followups = len(
            s.exec(select(FollowupRow).where(FollowupRow.status == "open")).all()
        )
        corrections = len(
            [c for c in s.exec(select(CorrectionRow)).all() if t0 <= aware(c.at) < t1]
        )
    orphans = [r for r in scans if r.submission_id is None]

    by: dict[str, dict] = defaultdict(lambda: {"forms": 0, "fields": 0, "eye": 0})
    reasons: dict[str, Counter] = defaultdict(Counter)
    for x in subs:
        name = value(x, "sender") or UNKNOWN
        by[name]["forms"] += 1
        by[name]["fields"] += len(x.fields)
    for x, f, fr in eye:
        name = value(x, "sender") or UNKNOWN
        by[name]["eye"] += 1
        reasons[name].update(fr.reasons or [fr.status])
    templates = bs.decision.reasons_he
    senders = []
    for name, d in by.items():
        top = reasons[name].most_common(1)
        code = top[0][0] if top else None
        senders.append(
            {
                "sender": name,
                "forms": d["forms"],
                "needs_eye_pct": (
                    round(100 * d["eye"] / d["fields"]) if d["fields"] else 0
                ),
                "top_reason": code,
                "top_reason_he": _generic(templates, code) if code else "",
            }
        )
    senders.sort(key=lambda r: (-r["needs_eye_pct"], -r["forms"]))

    per_day = Counter(aware(r.scanned_at).date() for r in orphans)
    orphans_by_day = [
        {"date": d.isoformat(), "day_he": DAYS_HE[d.weekday()], "count": per_day[d]}
        for d in days
    ]

    worst = next((r for r in senders if r["sender"] != UNKNOWN), None)
    if worst and worst["needs_eye_pct"]:
        t_senders = (
            f"{worst['sender']}: {worst['needs_eye_pct']}% מהשדות צריכים עין, "
            f"בעיקר {worst['top_reason_he'] or 'שדות חסרים'}"
        )
    else:
        t_senders = "כל השולחים שלחו טפסים שאומתו בלי מגע יד"
    if orphans:
        peak = max(orphans_by_day, key=lambda r: r["count"])
        t_orphans = (
            f"{len(orphans)} מבחנות הגיעו בלי טופס השבוע, "
            f"הכי הרבה ביום {peak['day_he']} ({peak['count']})"
        )
    else:
        t_orphans = "כל המבחנות השבוע הגיעו עם טופס"

    return {
        "from": days[0].isoformat(),
        "to": days[-1].isoformat(),
        "has_demo": any(b.demo for b in batches),
        "stats": {
            "hours_saved": round(max(0, saved_s) / 3600, 1),
            "forms": len(subs),
            "auto_approved_pct": round(100 * auto / len(subs)) if subs else 0,
            "tubes_without_form": len(orphans),
            "fields": fields_total,
            "fields_needing_eye": len(eye),
            "corrections": corrections,
            "open_followups": open_followups,
            "manual_seconds_per_form": s_.manual_seconds_per_form,
        },
        "by_sender": senders,
        "orphans_by_day": orphans_by_day,
        "takeaways": {"senders": t_senders, "orphans": t_orphans},
        "worst_sender": worst["sender"] if worst else None,
    }


def letter(bs, sender: str, end: date | None = None) -> dict:
    """A drafted letter to one sender about this week's forms. Text only:
    a person edits it and sends it from their own mail."""
    rep = weekly(bs, end)
    row = next((r for r in rep["by_sender"] if r["sender"] == sender), None)
    if row is None:
        raise KeyError(sender)
    issues = [row["top_reason_he"]] if row["top_reason_he"] else []
    text = (
        f"לכבוד {sender},\n\n"
        f"בשבוע {rep['from']} עד {rep['to']} קיבלנו מכם {row['forms']} טפסי "
        f"התחייבות. ב-{row['needs_eye_pct']}% מהשדות נדרשה בדיקה ידנית"
        + (f", בעיקר בגלל: {', '.join(issues)}" if issues else "")
        + ".\n\n"
        "כדי שהדגימות שלכם ייבדקו מהר יותר, נבקש:\n"
        "• להדביק את מדבקת הברקוד במקום המיועד ולא על הטקסט\n"
        "• למלא תאריך קבלה ומספר התחייבות בכתב ברור\n"
        "• לסרוק בבהירות מלאה, בלי צל ובלי קיפול\n\n"
        f"תודה על שיתוף הפעולה,\n{LAB}"
    )
    return {"sender": sender, "text": text}
