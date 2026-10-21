"""Follow-ups: things a person has to chase with the sender.

Created automatically when a batch finishes (missing fields, invalid ID) and
when a tube arrives without a form. A draft letter is written on request, for
a human to read, edit and send: nothing here sends anything.
"""

from datetime import datetime, timedelta, timezone

from sqlmodel import select

from vera.db import BatchRow, FollowupRow, now
from vera.evidence import FIELD_HE
from vera.views import value

OVERDUE = timedelta(hours=24)
STATUSES = ("open", "sent", "closed")
LAB = "המעבדה המרכזית לנגיפים"


def aware(dt: datetime | None) -> datetime | None:
    """SQLite drops the timezone; everything here is stored in UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def create_for_batch(bs, batch_id: str) -> int:
    """One follow-up per form with missing fields, one per invalid ID.
    Idempotent: running it again adds nothing."""
    with bs.sv.repo.session() as s:
        batch = s.get(BatchRow, batch_id)
        existing = {
            (f.submission_id, f.kind)
            for f in s.exec(
                select(FollowupRow).where(FollowupRow.batch_id == batch_id)
            ).all()
        }
        added = 0
        for sub in bs.submissions(batch_id):
            name = " ".join(
                v for v in (value(sub, "first_name"), value(sub, "last_name")) if v
            )
            who = name or f"מרכזי {value(sub, 'central_numbers') or '?'}"
            sender = value(sub, "sender") or None
            missing = [f for f, fr in sub.fields.items() if fr.status == "missing"]
            if missing and (sub.id, "missing_field") not in existing:
                names = ", ".join(FIELD_HE.get(f, f) for f in missing)
                s.add(
                    FollowupRow(
                        kind="missing_field",
                        batch_id=batch_id,
                        submission_id=sub.id,
                        field=",".join(missing),
                        sender=sender,
                        title_he=f"חסר בטופס של {who}: {names}",
                        detail_he=f"אצווה {batch.name}",
                        demo=batch.demo,
                    )
                )
                added += 1
            idf = sub.fields.get("id_number")
            bad_id = idf is not None and (
                {"checksum_failed", "validation_failed"} & set(idf.reasons)
            )
            if (
                bad_id
                and idf.status == "review"
                and (sub.id, "invalid_id") not in (existing)
            ):
                s.add(
                    FollowupRow(
                        kind="invalid_id",
                        batch_id=batch_id,
                        submission_id=sub.id,
                        field="id_number",
                        sender=sender,
                        title_he=f"ת.ז. לא תקינה בטופס של {who}",
                        detail_he=f"נקרא: {', '.join(idf.values) or '?'}",
                        demo=batch.demo,
                    )
                )
                added += 1
        s.commit()
    return added


def out(f: FollowupRow, at: datetime | None = None) -> dict:
    at = at or now()
    created = aware(f.created_at)
    age = at - created
    return {
        **f.model_dump(mode="json"),
        **{
            k: aware(getattr(f, k)).isoformat() if getattr(f, k) else None
            for k in ("created_at", "updated_at", "closed_at")
        },
        "age_hours": round(age.total_seconds() / 3600, 1),
        "overdue": f.status == "open" and age > OVERDUE,
    }


def list_followups(bs, status: str | None = None, demo: bool = True) -> list[dict]:
    with bs.sv.repo.session() as s:
        q = select(FollowupRow).order_by(FollowupRow.created_at.desc())
        if status:
            q = q.where(FollowupRow.status == status)
        if not demo:
            q = q.where(FollowupRow.demo == False)  # noqa: E712 (SQL expression)
        rows = s.exec(q).all()
    at = now()
    return [out(r, at) for r in rows]


def update(bs, fid: int, user: str, **changes) -> dict:
    with bs.sv.repo.session() as s:
        f = s.get(FollowupRow, fid)
        if f is None:
            raise KeyError(fid)
        status = changes.get("status")
        if status is not None:
            if status not in STATUSES:
                raise ValueError(f"status must be one of {STATUSES}")
            f.status = status
            f.closed_at = now() if status == "closed" else None
        for k in ("note", "draft"):
            if changes.get(k) is not None:
                setattr(f, k, changes[k])
        f.updated_at = now()
        s.add(f)
        bs.audit(s, user, "followup_updated", str(fid), **changes)
        s.commit()
        s.refresh(f)
        return out(f)


def facts(bs, f: FollowupRow) -> dict:
    """What a letter about this follow-up may say. Only what the lab already
    knows about the form; the model gets these facts and nothing else."""
    d = {"kind": f.kind, "sender": f.sender or "", "title": f.title_he}
    if f.submission_id:
        try:
            sub = bs.submission(f.submission_id)
        except KeyError:
            sub = None
        if sub:
            d.update(
                name=" ".join(
                    v for v in (value(sub, "first_name"), value(sub, "last_name")) if v
                ),
                central=value(sub, "central_numbers"),
                commitment=value(sub, "commitment_numbers"),
                id_read=value(sub, "id_number"),
                missing=[FIELD_HE.get(x, x) for x in (f.field or "").split(",") if x],
            )
    return d


def template_draft(d: dict) -> str:
    hello = f"לכבוד {d['sender']}," if d.get("sender") else "שלום רב,"
    ref = []
    if d.get("name"):
        ref.append(f"של {d['name']}")
    if d.get("central"):
        ref.append(f"(מספר מרכזי {d['central']})")
    ref = " ".join(ref)
    if d["kind"] == "missing_field":
        body = (
            f"בטופס ההתחייבות {ref} שהגיע אלינו חסרים הפרטים הבאים: "
            f"{', '.join(d.get('missing') or ['?'])}.\n"
            "נודה אם תשלחו אותם כדי שנוכל להשלים את קליטת הדגימה."
        )
    elif d["kind"] == "invalid_id":
        body = (
            f"מספר תעודת הזהות בטופס ההתחייבות {ref} ({d.get('id_read') or '?'}) "
            "אינו תקין.\nנודה אם תאמתו את המספר מול המטופל ותשלחו אותו שוב."
        )
    else:
        body = (
            f"{d['title']}.\n"
            "נודה אם תשלחו את טופס ההתחייבות המתאים כדי שנוכל לבדוק את הדגימה."
        )
    return f"{hello}\n\n{body}\n\nתודה,\n{LAB}"


def draft(bs, fid: int, assistant, user: str) -> dict:
    with bs.sv.repo.session() as s:
        f = s.get(FollowupRow, fid)
        if f is None:
            raise KeyError(fid)
    d = facts(bs, f)
    text, source = assistant.draft_letter(d) if assistant else (None, "template")
    if not text:
        text, source = template_draft(d), "template"
    update(bs, fid, user, draft=text)
    return {"draft": text, "source": source, "sent": False}
