"""Made-up history for the demo: `python -m vera.cli seed-demo`.

Four weeks of morning batches (Sunday-Thursday) with forms, corrections,
tubes and follow-ups, so the weekly report, follow-ups board and handover
have something to show. Everything is marked demo=true (the UI shows a
"נתוני דוגמה" badge) and `clear-demo` removes exactly that.

Synthetic values only, never from the sample forms (iron rule 5). Today's
batch is left open for review, with drawn form images, and with one shared
receipt-date stamp so "one fix that fixes everything" can be shown.
"""

import json
import random
import shutil
from datetime import date, datetime, time, timedelta, timezone

from sqlmodel import delete, select

from vera.db import BatchRow, CorrectionRow, DocumentRow, FollowupRow, PageRow
from vera.db import ShiftNoteRow, SubmissionRow, TubeScanRow
from vera.models import Correction, FieldResult, Source, Submission, worst_status

FIRST = ["נועה", "יוסף", "מרים", "דוד", "שרה", "אברהם", "רחל", "משה", "לאה", "אליהו"]
LAST = ["לוי", "מזרחי", "פרץ", "ביטון", "אברהם", "פרידמן", "אוחיון", "גולן", "שושן"]
# (name, share of fields that need an eye): one sender is clearly worse.
SENDERS = [
    ("כללית", 0.08),
    ("מכבי", 0.06),
    ("מאוחדת", 0.30),
    ("לאומית", 0.12),
    ("המרכז הרפואי שיבא", 0.10),
]
FIELDS = [
    "first_name",
    "last_name",
    "id_number",
    "central_numbers",
    "commitment_numbers",
    "receipt_date",
    "sender",
]
W, H = 1240, 1754  # A4 at 150 dpi
BOXES = {  # field -> (x1, y1, x2, y2) on the drawn form
    "first_name": (640, 300, 1140, 370),
    "last_name": (100, 300, 600, 370),
    "id_number": (640, 430, 1140, 500),
    "central_numbers": (100, 430, 600, 500),
    "commitment_numbers": (640, 560, 1140, 630),
    "receipt_date": (100, 560, 600, 630),
    "sender": (100, 170, 1140, 240),
}
LABELS = {
    "first_name": "שם פרטי",
    "last_name": "שם משפחה",
    "id_number": "ת.ז.",
    "central_numbers": "מספר מרכזי",
    "commitment_numbers": "מספר התחייבות",
    "receipt_date": "תאריך קבלה",
    "sender": "גורם שולח",
}


def valid_id(rng: random.Random) -> str:
    digits = [rng.randint(0, 9) for _ in range(8)]
    total = 0
    for i, d in enumerate(digits):
        x = d * (1 if i % 2 == 0 else 2)
        total += x - 9 if x > 9 else x
    return "".join(map(str, digits)) + str((10 - total % 10) % 10)


def _field(name, value, status, page_id, rng, alt=None) -> FieldResult:
    box = BOXES[name]
    src = lambda reader, v: Source(  # noqa: E731
        reader=reader,
        model=None if reader == "barcode" else "demo-vlm",
        values=[v],
        page_id=page_id,
        bbox=box,
        evidence_text=v,
    )
    if status == "missing":
        return FieldResult(
            field=name,
            values=[],
            status="missing",
            score=0.0,
            explanation_he="לא נמצא בעמוד",
        )
    if status == "verified":
        two = "barcode" if name in ("central_numbers", "commitment_numbers") else "ocr"
        return FieldResult(
            field=name,
            values=[value],
            status="verified",
            score=0.95,
            explanation_he=f"שני מקורות בלתי תלויים קראו אותו ערך ({value})",
            sources=[src("vlm_full", value), src(two, value)],
        )
    if status == "likely":
        return FieldResult(
            field=name,
            values=[value],
            status="likely",
            score=0.7,
            reasons=["source_single"],
            explanation_he="נקרא ממקור אחד בלבד (vlm_full), כדאי מבט מהיר",
            sources=[src("vlm_full", value)],
        )
    if name == "id_number":
        return FieldResult(
            field=name,
            values=[value],
            status="review",
            score=0.3,
            reasons=["checksum_failed"],
            explanation_he=f"ספרת הביקורת של ת.ז. {value} לא תקינה",
            sources=[src("vlm_full", value)],
        )
    if alt is None:
        alt = (
            value[:-1] + str((int(value[-1]) + 3) % 10) if value[-1:].isdigit() else ""
        )
    return FieldResult(
        field=name,
        values=[value],
        status="review",
        score=0.4,
        reasons=["disagree_sources"],
        explanation_he=f"המקורות לא מסכימים: {value}, {alt}",
        sources=[src("vlm_full", value), src("ocr", alt)],
        alternatives=[alt],
    )


def _draw(path, values: dict, rng: random.Random) -> None:
    """A plain synthetic form: sender header, labelled boxes, values."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    def font(size):
        for f in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ):
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                continue
        return ImageFont.load_default(size)

    def rtl(text):  # PIL has no bidi: draw Hebrew-only runs reversed
        return text[::-1] if any("\u0590" <= c <= "\u05ff" for c in text) else text

    img = Image.new("RGB", (W, H), (250, 249, 245))
    d = ImageDraw.Draw(img)
    d.text(
        (W - 100, 80),
        rtl("טופס התחייבות לבדיקת מעבדה"),
        fill=(30, 30, 30),
        font=font(40),
        anchor="ra",
    )
    for f, (x1, y1, x2, y2) in BOXES.items():
        d.rectangle((x1, y1, x2, y2), outline=(120, 120, 120), width=2)
        d.text(
            (x2 - 10, y1 - 34),
            rtl(LABELS[f]),
            fill=(90, 90, 90),
            font=font(24),
            anchor="ra",
        )
        v = values.get(f) or ""
        if v:
            d.text(
                (x2 - 16 if not v[0].isdigit() else x1 + 16, (y1 + y2) // 2),
                rtl(v),
                fill=(20, 30, 90),
                font=font(34),
                anchor="rm" if not v[0].isdigit() else "lm",
            )
    for i in range(14):  # body text lines
        y = 720 + i * 60
        d.line((100, y, rng.randint(600, 1140), y), fill=(200, 200, 200), width=6)
    d.rectangle((820, 1450, 1140, 1600), outline=(150, 60, 60), width=4)
    d.text((980, 1525), rtl("חותמת"), fill=(150, 60, 60), font=font(30), anchor="mm")
    img.filter(ImageFilter.GaussianBlur(0.6)).save(path)


def _form(rng, sub_id, batch_id, page_id, day, sender, bad, central, same):
    first, last = rng.choice(FIRST), rng.choice(LAST)
    values = {
        "first_name": first,
        "last_name": last,
        "id_number": valid_id(rng),
        "central_numbers": str(central),
        "commitment_numbers": str(rng.randint(10000, 99999)),
        "receipt_date": day.isoformat(),
        "sender": sender,
    }
    fields = {}
    for f in FIELDS:
        r = rng.random()
        status = "verified"
        if r < bad:
            status = rng.choice(["likely", "likely", "review", "missing"])
        if f == "receipt_date" and same:
            status = "likely"  # one stamp, read once per form
        if status == "review" and f == "id_number":
            values[f] = values[f][:-1] + str((int(values[f][-1]) + 1) % 10)
        if status == "missing":
            values[f] = ""
        fields[f] = _field(f, values[f], status, page_id, rng)
    sub = Submission(
        id=sub_id,
        batch_id=batch_id,
        page_ids=[page_id],
        profile_id="demo",
        page_kind="form",
        fields=fields,
        row_status=worst_status([fr.status for fr in fields.values()]),
    )
    return sub, values


def seed(bs, weeks: int = 4, seed_value: int = 7, today: date | None = None) -> dict:
    clear(bs)
    rng = random.Random(seed_value)
    today = today or datetime.now(timezone.utc).date()
    days = [
        today - timedelta(days=i)
        for i in range(weeks * 7 - 1, -1, -1)
        if (today - timedelta(days=i)).weekday() not in (4, 5)  # Fri, Sat
    ]
    central = 600000 + rng.randint(0, 9000)
    totals = {"batches": 0, "forms": 0, "tubes": 0}
    from vera.followups import create_for_batch

    for day in days:
        is_today = day == today
        bid = f"demo{day:%m%d}"
        created = datetime.combine(day, time(7, 30), tzinfo=timezone.utc)
        n = rng.randint(9, 16) if not is_today else 12
        subs, fixes = [], []
        with bs.sv.repo.session() as s:
            s.add(
                BatchRow(
                    id=bid,
                    name=f"בוקר {day:%d/%m}",
                    created_at=created,
                    ref_date=day,
                    status="done" if is_today else "exported",
                    locked=not is_today,
                    exported_at=None if is_today else created + timedelta(hours=3),
                    demo=True,
                )
            )
            doc = DocumentRow(
                id=f"{bid}-d",
                batch_id=bid,
                filename=f"scan-{day:%Y%m%d}.pdf",
                kind="pdf",
                sha256="demo",
                storage_key="",
                page_count=n,
            )
            s.add(doc)
            s.commit()
            work = bs.work_dir(bid) / "pages"
            if is_today:
                work.mkdir(parents=True, exist_ok=True)
            for i in range(n):
                sender, bad = rng.choice(SENDERS)
                pid = f"{bid}-p{i + 1:02d}"
                central += rng.randint(1, 4)
                sub, values = _form(
                    rng,
                    f"{bid}-{i + 1:02d}",
                    bid,
                    pid,
                    day,
                    sender,
                    bad,
                    central,
                    same=is_today and i < 9,
                )
                image = None
                if is_today:
                    image = f"pages/{pid}.png"
                    _draw(bs.work_dir(bid) / image, values, rng)
                s.add(
                    PageRow(
                        id=pid,
                        batch_id=bid,
                        document_id=doc.id,
                        page_no=i + 1,
                        status="done",
                        image=image,
                        width=W,
                        height=H,
                        quality={"sharpness": 120.0, "warnings": []},
                    )
                )
                if not is_today:  # the past was reviewed and exported
                    for f, fr in sub.fields.items():
                        if fr.status == "verified":
                            continue
                        new = fr.values or [values[f] or "?"]
                        at = created + timedelta(minutes=rng.randint(20, 150))
                        fr.history.append(
                            Correction(
                                user="lab",
                                at=at,
                                old_values=fr.values,
                                new_values=new,
                                via="ui",
                            )
                        )
                        fixes.append(
                            CorrectionRow(
                                submission_id=sub.id,
                                field=f,
                                user="lab",
                                at=at,
                                old_values=fr.values,
                                new_values=new,
                                action="edit",
                            )
                        )
                        fr.values, fr.status = new, "human"
                    # the row keeps its original status: it is the history of
                    # what the system decided on its own
                s.add(
                    SubmissionRow(
                        id=sub.id,
                        batch_id=bid,
                        row_status=sub.row_status,
                        position=i + 1,
                        data=json.loads(sub.model_dump_json()),
                        updated_at=created,
                    )
                )
                subs.append(sub)
            s.flush()  # corrections reference the submissions
            s.add_all(fixes)
            s.commit()
        totals["batches"] += 1
        totals["forms"] += n

        # tubes: most forms get theirs; now and then one comes alone
        scanned = rng.sample(subs, k=len(subs) - rng.randint(0, 2))
        if is_today:
            scanned = subs[:6]
        with bs.sv.repo.session() as s:
            slot = 0
            for sub in scanned:
                c = sub.fields["central_numbers"].values
                if not c:
                    continue
                s.add(
                    TubeScanRow(
                        batch_id=bid,
                        number=c[0],
                        raw=c[0],
                        submission_id=sub.id,
                        slot=slot,
                        scanned_at=created + timedelta(minutes=5 + slot),
                        demo=True,
                    )
                )
                slot += 1
            for _ in range(1 if is_today else rng.choice([0, 0, 1, 2])):
                num = str(central + rng.randint(500, 900))
                s.add(
                    TubeScanRow(
                        batch_id=bid,
                        number=num,
                        raw=num,
                        submission_id=None,
                        slot=slot,
                        scanned_at=created + timedelta(minutes=40),
                        demo=True,
                    )
                )
                s.add(
                    FollowupRow(
                        kind="tube_orphan",
                        batch_id=bid,
                        title_he=f"מבחנה {num} הגיעה בלי טופס",
                        detail_he=f"נסרקה באצווה בוקר {day:%d/%m}",
                        created_at=created + timedelta(minutes=40),
                        demo=True,
                    )
                )
                slot += 1
            totals["tubes"] += slot
            s.commit()

        create_for_batch(bs, bid)
        with bs.sv.repo.session() as s:  # age them; most old ones are done
            for f in s.exec(select(FollowupRow).where(FollowupRow.batch_id == bid)):
                f.created_at = created + timedelta(minutes=rng.randint(30, 90))
                age = (today - day).days
                if age > 3:
                    f.status = rng.choice(["closed", "closed", "closed", "sent"])
                elif age > 0:
                    f.status = rng.choice(["open", "sent", "closed"])
                if f.status == "closed":
                    f.closed_at = f.created_at + timedelta(hours=rng.randint(3, 40))
                s.add(f)
            s.commit()

    with bs.sv.repo.session() as s:
        s.add(
            ShiftNoteRow(
                author="רותי",
                at=datetime.combine(today, time(6, 45), tzinfo=timezone.utc),
                text=(
                    "שימי לב: ממאוחדת הגיעו אתמול שני טפסים בלי ת.ז., "
                    "ביקשתי שישלחו שוב."
                ),
                acked_by="דינה",
                acked_at=datetime.combine(today, time(7, 0), tzinfo=timezone.utc),
                demo=True,
            )
        )
        s.commit()
    return totals


def clear(bs) -> dict:
    with bs.sv.repo.session() as s:
        demo_rows = select(BatchRow).where(BatchRow.demo == True)  # noqa: E712
        bids = [b.id for b in s.exec(demo_rows)]
        subs = [
            x.id
            for x in s.exec(
                select(SubmissionRow).where(SubmissionRow.batch_id.in_(bids))
            )
        ]
        if subs:
            s.exec(delete(CorrectionRow).where(CorrectionRow.submission_id.in_(subs)))
        for model in (SubmissionRow, PageRow, DocumentRow):
            s.exec(delete(model).where(model.batch_id.in_(bids)))
        for model in (TubeScanRow, FollowupRow, ShiftNoteRow):
            s.exec(delete(model).where(model.demo == True))  # noqa: E712
        s.exec(delete(BatchRow).where(BatchRow.id.in_(bids)))
        s.commit()
    for bid in bids:
        shutil.rmtree(bs.work_dir(bid), ignore_errors=True)
    return {"batches_removed": len(bids)}
