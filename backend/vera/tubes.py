"""Tubes at the bench: a USB scanner types the tube number + Enter, the tube
is matched to a form by central number and goes into the next rack slot.

Rack: 6 columns (A-F) x 5 rows (1-5) seen from above = 30 slots. When it is
full the next tube starts a new rack. A tube with no form opens a follow-up.
"""

from sqlmodel import select

from vera.db import BatchRow, FollowupRow, TubeScanRow
from vera.followups import aware
from vera.views import brief, number_key

COLS, ROWS = "ABCDEF", 5
RACK = len(COLS) * ROWS


def slot_label(slot: int) -> str:
    i = slot % RACK
    return f"{COLS[i % len(COLS)]}{i // len(COLS) + 1}"


def _scan_out(row: TubeScanRow, subs: dict) -> dict:
    sub = subs.get(row.submission_id) if row.submission_id else None
    return {
        "id": row.id,
        "number": row.raw,
        "slot": row.slot,
        "rack": row.slot // RACK + 1,
        "label": slot_label(row.slot),
        "matched": row.submission_id is not None,
        "submission": brief(sub) if sub else None,
        "scanned_at": aware(row.scanned_at).isoformat(),
    }


def scan(bs, batch_id: str, raw: str, user: str) -> dict:
    key = number_key(raw)
    if not key:
        raise ValueError("מספר מבחנה ריק")
    subs = {s.id: s for s in bs.submissions(batch_id)}
    with bs.sv.repo.session() as s:
        batch = s.get(BatchRow, batch_id)
        if batch is None:
            raise KeyError(batch_id)
        rows = s.exec(select(TubeScanRow).where(TubeScanRow.batch_id == batch_id)).all()
        for r in rows:
            if r.number == key:  # scanned twice: show where it already is
                return {**_scan_out(r, subs), "duplicate": True}
        taken = {r.submission_id for r in rows if r.submission_id}
        match = next(
            (
                sub.id
                for sub in subs.values()
                if sub.id not in taken
                and any(
                    number_key(v) == key for v in sub.fields["central_numbers"].values
                )
            ),
            None,
        )
        row = TubeScanRow(
            batch_id=batch_id,
            number=key,
            raw=raw.strip()[:40],
            user=user,
            submission_id=match,
            slot=len(rows),
            demo=batch.demo,
        )
        s.add(row)
        if match is None:
            s.add(
                FollowupRow(
                    kind="tube_orphan",
                    batch_id=batch_id,
                    title_he=f"מבחנה {row.raw} הגיעה בלי טופס",
                    detail_he=f"נסרקה באצווה {batch.name}, תא {slot_label(row.slot)}",
                    demo=batch.demo,
                )
            )
        bs.audit(s, user, "tube_scanned", batch_id, number=row.raw, matched=match)
        s.commit()
        s.refresh(row)
        return {**_scan_out(row, subs), "duplicate": False}


def status(bs, batch_id: str) -> dict:
    subs = {s.id: s for s in bs.submissions(batch_id)}
    with bs.sv.repo.session() as s:
        if s.get(BatchRow, batch_id) is None:
            raise KeyError(batch_id)
        rows = s.exec(
            select(TubeScanRow)
            .where(TubeScanRow.batch_id == batch_id)
            .order_by(TubeScanRow.slot)
        ).all()
    scans = [_scan_out(r, subs) for r in rows]
    matched_ids = {r.submission_id for r in rows if r.submission_id}
    rack = (len(rows) - 1) // RACK if rows else 0
    in_rack = {x["slot"] % RACK: x for x in scans if x["slot"] // RACK == rack}
    newest = rows[-1].slot if rows else None
    slots = []
    for i in range(RACK):
        x = in_rack.get(i)
        slots.append(
            {
                "index": i,
                "label": slot_label(i),
                "number": x["number"] if x else None,
                "state": (
                    "empty" if x is None else "matched" if x["matched"] else "orphan"
                ),
                "newest": x is not None and x["slot"] == newest,
            }
        )
    return {
        "matched": [x for x in scans if x["matched"]],
        "orphans": [x for x in scans if not x["matched"]],
        "waiting": [brief(sub) for sid, sub in subs.items() if sid not in matched_ids],
        "rack": rack + 1,
        "slots": slots,
        "last": scans[-1] if scans else None,
    }
