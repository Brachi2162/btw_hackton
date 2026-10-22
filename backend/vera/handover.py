"""End-of-shift handover: what was done since the last handover, what is
still open, and a note from the team. The incoming person acknowledges it."""

from datetime import datetime, time, timezone

from sqlmodel import select

from vera.db import BatchRow, CorrectionRow, FollowupRow, PageRow, ShiftNoteRow
from vera.db import TubeScanRow, now
from vera.followups import OVERDUE, aware
from vera.views import brief


def _since(s) -> datetime:
    last = s.exec(
        select(ShiftNoteRow)
        .where(ShiftNoteRow.acked_at != None)  # noqa: E711 (SQL expression)
        .order_by(ShiftNoteRow.acked_at.desc())
    ).first()
    if last:
        return aware(last.acked_at)
    return datetime.combine(now().date(), time.min, tzinfo=timezone.utc)


# Fields that still wait for a person: yellow and orange. Grey (not on the
# form) and green are done, as on the review screen (D-018, D-025).
WAITING = ("likely", "review")


def overview(bs, batch_id: str | None = None) -> dict:
    """Counts for the sidebar and the handover: what is waiting now. With
    `batch_id`, also that batch's own waiting fields: the number the review
    screen of that batch shows, so the sidebar matches it."""
    with bs.sv.repo.session() as s:
        batches = s.exec(select(BatchRow).order_by(BatchRow.created_at.desc())).all()
        followups = s.exec(
            select(FollowupRow).where(FollowupRow.status == "open")
        ).all()
        matched = {
            r.submission_id
            for r in s.exec(select(TubeScanRow)).all()
            if r.submission_id
        }
    at = now()
    waiting, no_tube, open_batches = 0, 0, []
    in_batch = None
    for b in batches:
        if b.locked and b.id != batch_id:
            continue
        here = 0
        for sub in bs.submissions(b.id):
            here += sum(1 for fr in sub.fields.values() if fr.status in WAITING)
            if not b.locked:
                no_tube += sub.id not in matched
        if b.id == batch_id:
            in_batch = here
        if not b.locked:
            open_batches.append(b.id)
            waiting += here
    return {
        "review_waiting": waiting,
        "review_waiting_batch": in_batch,  # None without (or a wrong) batch_id
        "open_followups": len(followups),
        "overdue_followups": sum(at - aware(f.created_at) > OVERDUE for f in followups),
        "forms_waiting_for_tube": no_tube,
        "batches_not_exported": len(open_batches),
        "latest_batch_id": batches[0].id if batches else None,
        "has_demo": any(b.demo for b in batches),
        # intake: why pages can't be read now, and the scanner folder
        "vlm_problem": bs.vlm_problem(),
        "scan_folder": bs.s.scan_folder_host or str(bs.s.inbox_dir),
    }


def _note_out(n: ShiftNoteRow) -> dict:
    return {
        **n.model_dump(mode="json"),
        "at": aware(n.at).isoformat(),
        "acked_at": aware(n.acked_at).isoformat() if n.acked_at else None,
    }


def handover(bs) -> dict:
    with bs.sv.repo.session() as s:
        since = _since(s)
        fixed = [c for c in s.exec(select(CorrectionRow)).all() if aware(c.at) >= since]
        pages = s.exec(select(PageRow).where(PageRow.status == "done")).all()
        all_batches = s.exec(select(BatchRow)).all()
        exported = [
            b for b in all_batches if b.exported_at and aware(b.exported_at) >= since
        ]
        # Pages have no finish time; a page counts when its batch was
        # created since the last handover.
        recent = {b.id for b in all_batches if aware(b.created_at) >= since}
        tubes = [
            r for r in s.exec(select(TubeScanRow)).all() if aware(r.scanned_at) >= since
        ]
        notes = s.exec(select(ShiftNoteRow).order_by(ShiftNoteRow.at.desc())).all()[:10]
        batches = s.exec(select(BatchRow).where(BatchRow.locked == False)).all()  # noqa
    hot = []  # the orange fields left, so the next shift starts there
    for b in batches:
        for sub in bs.submissions(b.id):
            n = sum(1 for fr in sub.fields.values() if fr.status == "review")
            if n:
                hot.append({**brief(sub), "review_fields": n})
    unacked = next((n for n in notes if n.acked_at is None), None)
    return {
        "since": since.isoformat(),
        "done": {
            "pages_read": sum(1 for p in pages if p.batch_id in recent),
            "fields_fixed": len(fixed),
            "batches_exported": len(exported),
            "tubes_scanned": len(tubes),
        },
        "pending": overview(bs),
        "hot_forms": hot[:8],
        "notes": [_note_out(n) for n in notes],
        "open_handover": _note_out(unacked) if unacked else None,
    }


def add_note(bs, user: str, text: str) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("הערה ריקה")
    row = ShiftNoteRow(author=user, text=text[:2000], summary=overview(bs))
    with bs.sv.repo.session() as s:
        s.add(row)
        bs.audit(s, user, "handover_note", "shift")
        s.commit()
        s.refresh(row)
        return _note_out(row)


def ack(bs, note_id: int, user: str) -> dict:
    with bs.sv.repo.session() as s:
        row = s.get(ShiftNoteRow, note_id)
        if row is None:
            raise KeyError(note_id)
        if row.acked_at is None:
            row.acked_by, row.acked_at = user, now()
            s.add(row)
            bs.audit(s, user, "handover_ack", str(note_id))
            s.commit()
            s.refresh(row)
        return _note_out(row)
