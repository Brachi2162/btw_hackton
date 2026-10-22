"""Batch workflow on top of the pure pipeline: upload, process, correct.

The API is a thin layer over this module. Processing per page:
prepare (process pool) -> VLM read (thread) -> decide -> escalate the
pages that still have orange/grey fields -> decide again -> save + event.
Every state change is written to the database first, then published to the
batch's live event stream (SSE).
"""

import asyncio
import hashlib
import json
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlmodel import select

from vera.config import Settings
from vera.db import AuditRow, BatchRow, CorrectionRow, DocumentRow, PageRow
from vera.db import SubmissionRow, now
from vera.models import Correction, FieldResult, Source, Submission, worst_status
from vera.normalize import (
    norm_central,
    norm_commitment,
    norm_date,
    norm_id,
    norm_name,
)
from vera.pipeline.decide import FIELDS, MULTI, DecisionConfig, decide_page
from vera.pipeline.decide import merge_reads
from vera.pipeline.gvision import VisionOcr
from vera.pipeline.ingest import UnsupportedFileError, page_count, sniff_kind
from vera.pipeline.process import (
    crop_region,
    needs_crop,
    needs_escalation,
    prepare_page,
    read_crop,
    read_vlm,
)
from vera.pipeline.vlm import (
    CacheMiss,
    VlmInvalid,
    VlmCache,
    VlmConfig,
    filter_blocked,
    make_reader,
    provider_of,
    render_prompt,
    sender_names,
)
from vera.services import Services

log = logging.getLogger(__name__)

EXT = {
    "pdf": "pdf",
    "jpeg": "jpg",
    "png": "png",
    "heic": "heic",
    "tiff": "tif",
    "webp": "webp",
    "bmp": "bmp",
}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # spec 16: bound work per upload
# Fields where one confirmation is offered to the rest of the batch.
PROPAGATE_FIELDS = {"receipt_date"}
# Fields shared by many forms of one batch, where one fix of a misread is
# offered to the other forms with the same misread (e.g. "כללית" -> the
# hospital). Never names or IDs: two patients misread alike aren't alike.
FIX_PROPAGATE_FIELDS = {"sender", "receipt_date"}
# Fields whose past fixes are offered again when the same misread comes back
# (D-011): a misread sender recurs across batches. Not names or numbers: those
# belong to one patient or one form.
LEARN_FIELDS = {"sender"}
LEARN_MIN = 2  # a fix seen this many times becomes a suggestion


def guess_of(fr: FieldResult) -> str | None:
    """The readers' guess for an empty unreadable field (D-013): shown on the
    screen as a guess, never written as the value or exported."""
    if fr.status == "review" and not fr.values and fr.alternatives:
        if "illegible" in fr.reasons:
            return fr.alternatives[0]
    return None


def _field_summary(fr: FieldResult) -> dict:
    out = {"values": fr.values, "status": fr.status}
    guess = guess_of(fr)
    if guess:  # only when there is one: the usual shape stays as it was
        out["guess"] = guess
    return out


def page_error(exc: Exception) -> str:
    """Why a page failed, as the secretary sees it at intake."""
    text = str(exc)
    if filter_blocked(text):
        host = "api.anthropic.com" if "anthropic" in text else "כתובת מודל הראייה"
        return (
            f"סינון האינטרנט (NetFree) חסם את הפנייה למודל הראייה. "
            f"צריך לבקש מ-NetFree לפתוח את {host} לשימוש עבודה, ואז להעלות שוב."
        )
    if "credit balance is too low" in text or "exceeded your current quota" in text:
        return (
            "נגמר הקרדיט במפתח של מודל הראייה, ולכן העמוד לא נקרא. צריך להטעין "
            "את חשבון ה-API ואז ללחוץ 'נסי שוב'."
        )
    return f"{type(exc).__name__}: {text[:300]}"


def page_quality(image: Path, threshold: float) -> dict:
    """Sharpness = variance of the Laplacian on a ~1000px-wide grey copy.
    Low = blurred (phone photo, moved scan). Only a warning: the page is
    still read; the reader flags what it can't see."""
    import cv2

    img = cv2.imread(str(image), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return {"sharpness": None, "warnings": []}
    if img.shape[1] > 1000:
        f = 1000 / img.shape[1]
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    sharp = float(cv2.Laplacian(img, cv2.CV_64F).var())
    return {
        "sharpness": round(sharp, 1),
        "warnings": ["blur"] if sharp < threshold else [],
    }


class EventBus:
    """In-process fan-out of batch events to SSE subscribers."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, batch_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs[batch_id].add(q)
        return q

    def unsubscribe(self, batch_id: str, q: asyncio.Queue) -> None:
        self._subs[batch_id].discard(q)

    def publish(self, batch_id: str, event: str, data: dict) -> None:
        for q in list(self._subs.get(batch_id, ())):
            try:
                q.put_nowait({"event": event, "data": data})
            except asyncio.QueueFull:  # a stuck client must not block work
                pass


class ValidationError(ValueError):
    """A correction that fails the field's checks (shown to the user)."""


@dataclass
class Readers:
    full: object
    escalated: object
    prompt: str
    crop: object = None
    crop_prompt: str = ""
    crop_version: str = ""
    crop_max_side: int = 1568
    crop_zoom: bool = True
    crop_check: object = None  # another provider on the same crop (D-012)
    ocr: object = None  # VisionOcr (D-009), when there is a key or a cache


class BatchService:
    def __init__(self, services: Services, events: EventBus | None = None):
        self.sv = services
        self.s: Settings = services.settings
        self.events = events or EventBus()
        self.decision = DecisionConfig.load(self.s.config_dir)
        self._readers: Readers | None = None
        self.dpi, self.orient = 300, True  # tests turn these down
        # Pages a running process_batch has taken: a second upload to the
        # same batch must not read them again (the queue is in-process).
        self._claimed: set[str] = set()
        self.recover_interrupted()

    def recover_interrupted(self) -> int:
        """At startup no job runs, so a page still queued or processing was
        cut off (a restart mid-batch). It becomes failed with the reason, and
        "נסי שוב" (retry-failed) reads it again."""
        with self.sv.repo.session() as s:
            stuck = s.exec(
                select(PageRow).where(PageRow.status.in_(["queued", "processing"]))
            ).all()
            for p in stuck:
                p.status, p.stage = "failed", None
                p.error = "העיבוד נקטע כי השרת הופעל מחדש. אפשר ללחוץ 'נסי שוב'."
                s.add(p)
            for b in s.exec(select(BatchRow).where(BatchRow.status == "processing")):
                b.status = "done"
                s.add(b)
            s.commit()
        return len(stuck)

    # --- helpers ---

    def work_dir(self, batch_id: str) -> Path:
        return self.s.data_dir / "work" / batch_id

    def readers(self) -> Readers:
        if self._readers is None:
            cfg = VlmConfig.load(self.s.config_dir)
            cache = VlmCache(self.s.data_dir / "cache" / "vlm")
            mode = self.s.vlm_mode
            keys = {
                "api_key": self.s.anthropic_api_key,
                "gemini_api_key": self.s.gemini_api_key,
            }
            self._readers = Readers(
                full=make_reader(mode, cfg, cache, **keys),
                escalated=make_reader(
                    mode, cfg, cache, model=cfg.escalation_model, **keys
                ),
                prompt=render_prompt(
                    self.s.prompts_dir,
                    cfg.prompt,
                    cfg.commitment_aliases,
                    sender_names(self.s.config_dir),
                ),
                crop=(
                    make_reader(mode, cfg, cache, model=cfg.crop_model, **keys)
                    if cfg.crop_model
                    else None
                ),
                crop_prompt=(
                    (self.s.prompts_dir / f"{cfg.crop_prompt}.md").read_text(
                        encoding="utf-8"
                    )
                    if cfg.crop_prompt
                    else ""
                ),
                crop_version=cfg.crop_prompt,
                crop_max_side=cfg.crop_max_side,
                crop_zoom=cfg.crop_zoom,
                crop_check=(
                    make_reader(mode, cfg, cache, model=cfg.crop_check_model, **keys)
                    if cfg.crop_model and cfg.crop_check_model
                    else None
                ),
                ocr=(
                    VisionOcr(
                        self.s.data_dir / "cache" / "gvision",
                        self.s.google_vision_api_key,
                        mode=mode,
                    )
                    if mode == "replay" or self.s.google_vision_api_key
                    else None
                ),
            )
        return self._readers

    def vlm_problem(self) -> str | None:
        """Why pages can't be read right now (shown at intake), or None."""
        if self._readers is not None:  # set up already (or injected by tests)
            return None
        if self.s.vlm_mode == "live":
            cfg = VlmConfig.load(self.s.config_dir)
            models = [
                cfg.model,
                cfg.escalation_model,
                cfg.crop_model,
                cfg.crop_check_model,
            ]
            have = {
                "anthropic": self.s.anthropic_api_key,
                "gemini": self.s.gemini_api_key,
            }
            missing = sorted(
                {
                    f"{p.upper()}_API_KEY"
                    for p in map(provider_of, filter(None, models))
                    if not have[p]
                }
            )
            if missing:
                return (
                    f"אין מפתח למודל הראייה: צריך להוסיף {', '.join(missing)} "
                    "לקובץ .env ולהפעיל מחדש (docker compose up -d)"
                )
        cache = self.s.data_dir / "cache" / "vlm"
        if self.s.vlm_mode == "replay" and not any(cache.glob("**/*.json")):
            return "מצב replay בלי מטמון קריאות: אין ממה לקרוא את העמודים"
        return None

    def audit(self, session, user: str, action: str, target: str, **detail) -> None:
        session.add(AuditRow(user=user, action=action, target=target, detail=detail))

    # --- batches and files ---

    def create_batch(self, name: str, ref_date: date | None, user: str) -> BatchRow:
        batch = BatchRow(
            id=uuid.uuid4().hex[:12], name=name, ref_date=ref_date or date.today()
        )
        with self.sv.repo.session() as s:
            s.add(batch)
            self.audit(s, user, "batch_created", batch.id, name=name)
            s.commit()
            s.refresh(batch)
        return batch

    def add_file(self, batch_id: str, filename: str, data: bytes, user: str) -> dict:
        if len(data) > MAX_UPLOAD_BYTES:
            raise UnsupportedFileError("הקובץ גדול מ-50MB")
        kind = sniff_kind(data[:16])  # by content, never by extension (spec 16)
        with self.sv.repo.session() as s:
            batch = s.get(BatchRow, batch_id)
            if batch is None:
                raise KeyError(batch_id)
            if batch.locked:
                raise PermissionError("batch is exported and locked")
            doc_id = uuid.uuid4().hex[:12]
            key = f"{batch_id}/{doc_id}.{EXT[kind]}"
            self.sv.storage.put(key, data)
            src = self.work_dir(batch_id) / "src" / f"{doc_id}.{EXT[kind]}"
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_bytes(data)
            try:
                count = page_count(src)
            except UnsupportedFileError:
                src.unlink(missing_ok=True)
                self.sv.storage.delete(key)
                raise
            seq = len(
                s.exec(
                    select(DocumentRow.id).where(DocumentRow.batch_id == batch_id)
                ).all()
            )
            doc = DocumentRow(
                seq=seq,
                id=doc_id,
                batch_id=batch_id,
                filename=Path(filename).name[:200],
                kind=kind,
                sha256=hashlib.sha256(data).hexdigest(),
                storage_key=key,
                page_count=count,
            )
            s.add(doc)
            for i in range(count):
                s.add(
                    PageRow(
                        id=f"{doc_id}-p{i + 1:02d}",
                        batch_id=batch_id,
                        document_id=doc_id,
                        page_no=i + 1,
                    )
                )
            self.audit(s, user, "file_added", batch_id, file=doc.filename, pages=count)
            s.commit()
            return {"document_id": doc_id, "filename": doc.filename, "pages": count}

    def requeue_failed(self, batch_id: str, user: str) -> int:
        """Failed pages back in the queue (e.g. after a network block), so
        `process_batch` reads only them; pages already read stay as they are."""
        with self.sv.repo.session() as s:
            batch = s.get(BatchRow, batch_id)
            if batch is None:
                raise KeyError(batch_id)
            if batch.locked:
                raise PermissionError("batch is exported and locked")
            pages = s.exec(
                select(PageRow).where(
                    PageRow.batch_id == batch_id, PageRow.status == "failed"
                )
            ).all()
            for p in pages:
                p.status, p.error, p.stage = "queued", None, None
                s.add(p)
            if pages:
                self.audit(s, user, "pages_retried", batch_id, pages=len(pages))
            s.commit()
            return len(pages)

    # --- processing ---

    async def process_batch(self, batch_id: str) -> dict:
        with self.sv.repo.session() as s:
            batch = s.get(BatchRow, batch_id)
            batch.status = "processing"
            s.add(batch)
            s.commit()
            ref = batch.ref_date
            pages = s.exec(
                select(PageRow, DocumentRow)
                .where(PageRow.batch_id == batch_id, PageRow.status == "queued")
                .join(DocumentRow, PageRow.document_id == DocumentRow.id)
            ).all()
            jobs = [
                (p.id, d.id, d.kind, p.page_no)
                for p, d in pages
                if p.id not in self._claimed
            ]
            self._claimed.update(j[0] for j in jobs)

        problem = self.vlm_problem()
        if problem:
            # Without a reader every page would fail after the costly prepare
            # step; fail them now, with the reason, so the screen can say so.
            for page_id, *_ in jobs:
                self._set_page(page_id, status="failed", error=problem)
                self._claimed.discard(page_id)
            self.events.publish(
                batch_id, "warning", {"error": "vlm", "message": problem}
            )
            jobs = []

        sem = asyncio.Semaphore(max(1, self.s.max_concurrent_jobs))

        async def one(job):
            try:
                async with sem:
                    await self._process_page(batch_id, ref, *job)
            finally:
                self._claimed.discard(job[0])

        await asyncio.gather(*(one(j) for j in jobs))
        with self.sv.repo.session() as s:
            batch = s.get(BatchRow, batch_id)
            batch.status = "done"
            s.add(batch)
            s.commit()
        from vera.followups import create_for_batch

        create_for_batch(self, batch_id)
        counts = self.counts(batch_id)
        self.events.publish(batch_id, "batch_done", counts)
        return counts

    async def _process_page(self, batch_id, ref, page_id, doc_id, kind, page_no):
        src = self.work_dir(batch_id) / "src" / f"{doc_id}.{EXT[kind]}"
        try:
            self._set_page(page_id, status="processing")
            self._stage(batch_id, page_id, doc_id, "split")
            prep = await self.sv.jobs.run_cpu(
                prepare_page,
                src,
                page_no - 1,
                self.work_dir(batch_id),
                self.dpi,
                self.orient,
            )
            rec = prep["record"]
            self._set_page(
                page_id,
                sha256=rec["sha256"],
                rotation=rec["rotation"],
                image=rec["image"],
                width=rec["width"],
                height=rec["height"],
                barcodes=prep["barcodes"],
            )
            r = self.readers()
            image = self.work_dir(batch_id) / rec["image"]
            quality = await asyncio.to_thread(
                page_quality, image, self.s.blur_threshold
            )
            self._set_page(page_id, quality=quality)
            if quality["warnings"]:
                self.events.publish(
                    batch_id,
                    "warning",
                    {"page_id": page_id, "error": "blur", **quality},
                )
            self._stage(batch_id, page_id, doc_id, "read")
            vlm = await asyncio.to_thread(
                read_vlm, r.full, image, rec["sha256"], r.prompt, page_id
            )
            self._set_page(page_id, vlm=vlm)
            ocr = None
            if r.ocr is not None:
                ocr = await asyncio.to_thread(self._ocr, r.ocr, image, rec["sha256"])
                self._set_page(page_id, ocr=ocr)
            lines = ocr.get("lines") if ocr else None
            self._stage(batch_id, page_id, doc_id, "verify")
            sub = decide_page(
                page_id, batch_id, vlm, prep["barcodes"], self.decision, ref, ocr=lines
            )
            if needs_escalation(sub):
                esc = await asyncio.to_thread(
                    read_vlm,
                    r.escalated,
                    image,
                    rec["sha256"],
                    r.prompt,
                    page_id,
                    "vlm_escalated",
                )
                self._set_page(page_id, vlm_escalated=esc)
                sub = decide_page(
                    page_id,
                    batch_id,
                    merge_reads(vlm, esc),
                    prep["barcodes"],
                    self.decision,
                    ref,
                    ocr=lines,
                )
            else:
                esc = None
            if r.crop is not None and needs_crop(sub):
                merged = merge_reads(vlm, esc)
                crop = await asyncio.to_thread(
                    read_crop,
                    r.crop,
                    image,
                    rec["sha256"],
                    r.crop_prompt,
                    r.crop_version,
                    r.crop_max_side,
                    page_id,
                    crop_region(merged),
                    r.crop_zoom,
                )
                check = None
                if r.crop_check is not None and "error" not in crop:
                    check = await asyncio.to_thread(
                        read_crop,
                        r.crop_check,
                        image,
                        rec["sha256"],
                        r.crop_prompt,
                        r.crop_version,
                        r.crop_max_side,
                        page_id,
                        crop_region(merged),
                        box=tuple(crop["box"]),
                        reader_name="vlm_crop_check",
                    )
                # One column for both: the check read rides along with the crop.
                self._set_page(page_id, vlm_crops={**crop, "check": check})
                sub = decide_page(
                    page_id,
                    batch_id,
                    merge_reads(vlm, esc, crop, check),
                    prep["barcodes"],
                    self.decision,
                    ref,
                    ocr=lines,
                )
            self._save_submission(sub, position=self._position(doc_id, page_no))
            self._set_page(page_id, status="done")
            self.events.publish(
                batch_id,
                "page_done",
                {"page_id": page_id, "row_status": sub.row_status},
            )
            self.events.publish(batch_id, "submission_updated", self.summary(sub))
        except Exception as exc:  # one bad page must not sink the batch
            log.exception("page %s failed", page_id)
            self._set_page(page_id, status="failed", error=page_error(exc))
            self.events.publish(
                batch_id, "warning", {"page_id": page_id, "error": type(exc).__name__}
            )

    @staticmethod
    def _ocr(reader: VisionOcr, image: Path, sha256: str) -> dict:
        """Vision OCR is an extra source (D-009): when it fails, the page is
        still read and decided without it."""
        from PIL import Image

        try:
            with Image.open(image) as img:
                return {"lines": reader.read(img, sha256)}
        except (VlmInvalid, CacheMiss) as e:
            log.warning("ocr skipped: %s", page_error(e)[:200])
            return {"error": type(e).__name__, "detail": page_error(e)[:300]}

    def _stage(self, batch_id: str, page_id: str, doc_id: str, stage: str) -> None:
        self._set_page(page_id, stage=stage)
        self.events.publish(
            batch_id,
            "page_stage",
            {"page_id": page_id, "document_id": doc_id, "stage": stage},
        )

    def _position(self, doc_id: str, page_no: int) -> int:
        with self.sv.repo.session() as s:
            return s.get(DocumentRow, doc_id).seq * 1000 + page_no

    def _set_page(self, page_id: str, **values) -> None:
        with self.sv.repo.session() as s:
            page = s.get(PageRow, page_id)
            for k, v in values.items():
                setattr(page, k, v)
            s.add(page)
            s.commit()

    def redecide(self, batch_id: str | None = None) -> int:
        """Decide the read pages again from their stored reads, under the
        rules as they are now: no model call, so no cost. What a person did
        stays: a confirmed or corrected field, and a place marked by hand.
        Exported (locked) batches are left alone."""
        with self.sv.repo.session() as s:
            q = select(PageRow, BatchRow).join(
                BatchRow, PageRow.batch_id == BatchRow.id
            )
            q = q.where(PageRow.status == "done", BatchRow.locked.is_(False))
            if batch_id:
                q = q.where(PageRow.batch_id == batch_id)
            rows = s.exec(q).all()
        done = 0
        for p, batch in rows:
            sub_id = f"{p.batch_id}:{p.id}"
            try:
                old = self.submission(sub_id)
            except KeyError:
                continue
            if old.page_ids != [p.id]:
                continue  # a form joined from several pages: leave it as is
            crops = dict(p.vlm_crops or {})
            check = crops.pop("check", None)
            new = decide_page(
                p.id,
                p.batch_id,
                merge_reads(p.vlm, p.vlm_escalated or None, crops or None, check),
                p.barcodes,
                self.decision,
                batch.ref_date,
                ocr=(p.ocr or {}).get("lines"),
            )
            for f, fr in old.fields.items():
                if fr.status == "human" or fr.history:
                    new.fields[f] = fr
                    continue
                marked = [x for x in fr.sources if x.reader == "human"]
                new.fields[f].sources += marked
            new.row_status = worst_status([f.status for f in new.fields.values()])
            self._save_submission(new)
            done += 1
        from vera.followups import create_for_batch

        for bid in {p.batch_id for p, _ in rows}:
            create_for_batch(self, bid)
        return done

    def _save_submission(self, sub: Submission, position: int | None = None) -> None:
        with self.sv.repo.session() as s:
            row = s.get(SubmissionRow, sub.id) or SubmissionRow(
                id=sub.id, batch_id=sub.batch_id, row_status=sub.row_status
            )
            row.row_status = sub.row_status
            row.data = json.loads(sub.model_dump_json())
            row.updated_at = now()
            if position is not None:
                row.position = position
            s.add(row)
            s.commit()

    # --- reading ---

    def summary(self, sub: Submission) -> dict:
        return {
            "id": sub.id,
            "page_ids": sub.page_ids,
            "row_status": sub.row_status,
            "fields": {f: _field_summary(fr) for f, fr in sub.fields.items()},
        }

    def submissions(self, batch_id: str) -> list[Submission]:
        with self.sv.repo.session() as s:
            rows = s.exec(
                select(SubmissionRow)
                .where(SubmissionRow.batch_id == batch_id)
                .order_by(SubmissionRow.position)
            ).all()
            return [Submission.model_validate(r.data) for r in rows]

    def submission(self, sub_id: str) -> Submission:
        with self.sv.repo.session() as s:
            row = s.get(SubmissionRow, sub_id)
            if row is None:
                raise KeyError(sub_id)
            return Submission.model_validate(row.data)

    def counts(self, batch_id: str) -> dict:
        with self.sv.repo.session() as s:
            pages = s.exec(select(PageRow.status).where(PageRow.batch_id == batch_id))
            page_counts = defaultdict(int)
            for st in pages.all():
                page_counts[st] += 1
        fields = defaultdict(int)
        rows = defaultdict(int)
        for sub in self.submissions(batch_id):
            rows[sub.row_status] += 1
            for fr in sub.fields.values():
                fields[fr.status] += 1
        return {"pages": dict(page_counts), "rows": dict(rows), "fields": dict(fields)}

    # --- corrections (spec 11.5) ---

    def _validate(self, field: str, values: list[str], ref: date) -> list[str]:
        out = []
        for v in values:
            v = v.strip()
            if not v:
                continue
            if field in ("first_name", "last_name"):
                n = norm_name(v)
            elif field == "id_number":
                n = norm_id(v)
            elif field == "central_numbers":
                n = norm_central(v)
            elif field == "commitment_numbers":
                n = norm_commitment(v)
            elif field == "receipt_date":
                n = norm_date(v, ref, self.decision.max_age_days)
            elif field == "sender":
                sid = self.decision.senders.match(v)
                if sid is None:
                    raise ValidationError("השולח לא נמצא ברשימת השולחים")
                out.append(self.decision.senders.name(sid))
                continue
            else:
                raise ValidationError(f"שדה לא מוכר: {field}")
            if n.value is None or n.problems:
                raise ValidationError(f"{v}: לא עומד בכללי השדה ({n.rule})")
            out.append(n.value)
        if len(out) > 1 and field not in MULTI:
            raise ValidationError("בשדה הזה מותר ערך אחד בלבד")
        return out

    def correct(
        self,
        sub_id: str,
        field: str,
        values: list[str] | None,
        user: str,
        force: bool = False,
        via: str = "ui",
        explanation: str | None = None,
    ) -> Submission:
        """Edit (values given) or accept (values None) one field. Checks run
        on the server; `force` saves a value that fails them, and says so in
        the log (e.g. an ID whose check digit really is wrong on the form)."""
        if field not in FIELDS:
            raise ValidationError(f"שדה לא מוכר: {field}")
        with self.sv.repo.session() as s:
            row = s.get(SubmissionRow, sub_id)
            if row is None:
                raise KeyError(sub_id)
            batch = s.get(BatchRow, row.batch_id)
            if batch.locked:
                raise PermissionError("batch is exported and locked")
            sub = Submission.model_validate(row.data)
            fr: FieldResult = sub.fields[field]
            old = list(fr.values)
            if values is None:
                new, action = old, "accept"
            else:
                try:
                    new = self._validate(field, values, batch.ref_date)
                except ValidationError:
                    if not force:
                        raise
                    new = [v.strip() for v in values if v.strip()]
                action = "edit"
            fr.history.append(
                Correction(user=user, at=now(), old_values=old, new_values=new, via=via)
            )
            fr.values, fr.status = new, "human"
            fr.explanation_he = explanation or (
                "אושר ידנית" if action == "accept" else "תוקן ידנית"
            )
            sub.row_status = worst_status([f.status for f in sub.fields.values()])
            row.data = json.loads(sub.model_dump_json())
            row.row_status = sub.row_status
            row.updated_at = now()
            s.add(row)
            s.add(
                CorrectionRow(
                    submission_id=sub_id,
                    field=field,
                    user=user,
                    old_values=old,
                    new_values=new,
                    via=via,
                    action=action,
                )
            )
            self.audit(
                s,
                user,
                f"field_{action}",
                sub_id,
                field=field,
                old=old,
                new=new,
                forced=force and action == "edit",
            )
            s.commit()
        self.events.publish(sub.batch_id, "submission_updated", self.summary(sub))
        return sub

    # --- learning from people (D-011) ---

    def learned_fixes(self, field: str, values: list[str]) -> list[dict]:
        """Past fixes of this exact misread, most frequent first: offered as
        options, never applied by themselves (iron rule 1)."""
        if field not in LEARN_FIELDS or not values:
            return []
        with self.sv.repo.session() as s:
            rows = s.exec(
                select(CorrectionRow).where(
                    CorrectionRow.field == field, CorrectionRow.action == "edit"
                )
            ).all()
        seen: dict[tuple, int] = defaultdict(int)
        for r in rows:
            if r.old_values == values and r.new_values and r.new_values != values:
                seen[tuple(r.new_values)] += 1
        return [
            {"value": ", ".join(v), "count": n}
            for v, n in sorted(seen.items(), key=lambda kv: -kv[1])
            if n >= LEARN_MIN
        ]

    def set_location(
        self, sub_id: str, field: str, page_id: str, bbox: list[int], user: str
    ) -> Submission:
        """A person marks where the field is on this form's page: it becomes
        the field's exact box (a human source). Only this form: layouts differ
        from form to form, even from one sender."""
        if field not in FIELDS:
            raise ValidationError(f"שדה לא מוכר: {field}")
        with self.sv.repo.session() as s:
            row = s.get(SubmissionRow, sub_id)
            if row is None:
                raise KeyError(sub_id)
            sub = Submission.model_validate(row.data)
            page = s.get(PageRow, page_id)
            if page is None or page_id not in sub.page_ids or not page.width:
                raise ValidationError("העמוד לא שייך לטופס הזה")
            x1, y1, x2, y2 = (int(v) for v in bbox)
            x1, x2 = sorted((max(0, x1), min(page.width, x2)))
            y1, y2 = sorted((max(0, y1), min(page.height, y2)))
            if x2 - x1 < 8 or y2 - y1 < 8:
                raise ValidationError("המלבן קטן מדי")
            fr = sub.fields[field]
            fr.sources = [
                x for x in fr.sources if not (x.reader == "human" and x.bbox)
            ] + [
                Source(
                    reader="human",
                    values=fr.values,
                    page_id=page_id,
                    bbox=(x1, y1, x2, y2),
                    evidence_text=f"המיקום סומן ידנית ({user})",
                )
            ]
            row.data = json.loads(sub.model_dump_json())
            row.updated_at = now()
            s.add(row)
            self.audit(
                s, user, "field_location", sub_id, field=field, bbox=[x1, y1, x2, y2]
            )
            s.commit()
        return sub

    # --- "one fix that fixes everything" ---

    def propagation(self, sub: Submission, field: str) -> dict | None:
        """After a human confirms a value: other forms in the batch whose
        same field is `likely` with the same value (e.g. one receipt-date
        stamp on the whole morning's pile). After a human fixes a misread:
        other forms with the same misread in that field (kind "fix")."""
        fr = sub.fields[field]
        last = fr.history[-1] if fr.history else None
        if (
            field in FIX_PROPAGATE_FIELDS
            and last
            and last.old_values
            and last.old_values != last.new_values
            and len(fr.values) == 1
        ):
            ids = [
                other.id
                for other in self.submissions(sub.batch_id)
                if other.id != sub.id
                and other.fields[field].status != "human"
                and other.fields[field].values == last.old_values
            ]
            if ids:
                return {
                    "field": field,
                    "value": fr.values[0],
                    "from_values": last.old_values,
                    "kind": "fix",
                    "count": len(ids),
                    "submission_ids": ids,
                }
            return None
        if field not in PROPAGATE_FIELDS:
            return None
        values = fr.values
        if len(values) != 1:
            return None
        ids = [
            other.id
            for other in self.submissions(sub.batch_id)
            if other.id != sub.id
            and other.fields[field].status == "likely"
            and other.fields[field].values == values
        ]
        if not ids:
            return None
        return {
            "field": field,
            "value": values[0],
            "from_values": None,
            "kind": "accept",
            "count": len(ids),
            "submission_ids": ids,
        }

    def propagate(
        self,
        batch_id: str,
        field: str,
        value: str,
        ids: list[str],
        user: str,
        from_values: list[str] | None = None,
    ) -> dict:
        """Apply a confirmed value to forms that still hold it as `likely`,
        or (from_values given) the same fix to forms with the same misread.
        Each one is a normal correction (source human, in `corrections`)."""
        applied, skipped = [], []
        for sid in ids:
            try:
                sub = self.submission(sid)
            except KeyError:
                skipped.append(sid)
                continue
            fr = sub.fields.get(field)
            if from_values:
                fits = (
                    field in FIX_PROPAGATE_FIELDS
                    and fr is not None
                    and fr.status != "human"
                    and fr.values == from_values
                )
            else:
                fits = fr is not None and fr.status == "likely" and fr.values == [value]
            if sub.batch_id != batch_id or not fits:
                skipped.append(sid)  # changed since the suggestion was made
                continue
            if from_values:
                self.correct(
                    sid,
                    field,
                    [value],
                    user,
                    explanation=f"תוקן בהחלה: אותה טעות ({', '.join(from_values)})",
                )
            else:
                self.correct(
                    sid,
                    field,
                    None,
                    user,
                    explanation="אושר בהחלה על כל הטפסים עם אותה חותמת",
                )
            applied.append(sid)
        with self.sv.repo.session() as s:
            self.audit(
                s,
                user,
                "propagated",
                batch_id,
                field=field,
                value=value,
                from_values=from_values,
                n=len(applied),
            )
            s.commit()
        return {"applied": applied, "skipped": skipped, "counts": self.counts(batch_id)}

    # --- review queue and search (spec 11.10, 11.13) ---

    def review_queue(self, batch_id: str) -> list[dict]:
        """Only the problem fields: missing, then conflicts, then low
        confidence; grouped by form in batch order."""
        rank = {"missing": 0, "review": 1, "likely": 2}
        items = []
        for pos, sub in enumerate(self.submissions(batch_id)):
            for f, fr in sub.fields.items():
                if fr.status in rank:
                    items.append(
                        {
                            "submission_id": sub.id,
                            "page_ids": sub.page_ids,
                            "field": f,
                            "status": fr.status,
                            "values": fr.values,
                            "alternatives": fr.alternatives,
                            "explanation_he": fr.explanation_he,
                            "_key": (rank[fr.status], pos),
                        }
                    )
        items.sort(key=lambda i: i.pop("_key"))
        return items

    def search(self, q: str) -> list[dict]:
        """By central / commitment / ID number, e.g. from a USB barcode
        scanner that types the tube's number + Enter."""
        key = "".join(ch for ch in q.upper() if ch.isalnum()).lstrip("0")
        if not key:
            return []
        hits = []
        with self.sv.repo.session() as s:
            rows = s.exec(select(SubmissionRow)).all()
        for row in rows:
            sub = Submission.model_validate(row.data)
            for f in ("central_numbers", "commitment_numbers", "id_number"):
                if any(v.upper().lstrip("0") == key for v in sub.fields[f].values):
                    hits.append(
                        {"submission_id": sub.id, "batch_id": sub.batch_id, "field": f}
                    )
        return hits

    # --- export (spec 6) ---

    def export(self, batch_id: str, user: str, force: bool = False) -> bytes:
        """xlsx of the batch. Blocked while orange fields are unapproved,
        unless forced (logged; the cells stay coloured). Locks the batch and
        verifies the file against the database before returning it."""
        from vera.export_xlsx import (
            blocking_fields,
            build_workbook,
            verify_export,
        )

        subs = self.submissions(batch_id)
        blocked = blocking_fields(subs)
        if blocked and not force:
            raise ExportBlocked(blocked)
        with self.sv.repo.session() as s:
            batch = s.get(BatchRow, batch_id)
            if batch is None:
                raise KeyError(batch_id)
            docs = {
                d.id: d.filename
                for d in s.exec(
                    select(DocumentRow).where(DocumentRow.batch_id == batch_id)
                ).all()
            }
            pages = {
                p.id: (p.document_id, p.page_no)
                for p in s.exec(select(PageRow).where(PageRow.batch_id == batch_id))
            }
            corrections = [
                c.model_dump()
                for c in s.exec(
                    select(CorrectionRow)
                    .where(CorrectionRow.submission_id.in_([x.id for x in subs]))
                    .order_by(CorrectionRow.at)
                ).all()
            ]
            name = batch.name
        sources = {
            sub.id: ", ".join(
                f"{docs.get(pages[p][0], '?')} עמ' {pages[p][1]}"
                for p in sub.page_ids
                if p in pages
            )
            for sub in subs
        }
        data = build_workbook(subs, corrections, sources, name, forced=bool(blocked))
        problems = verify_export(data, subs)
        if problems:  # never hand out a file that differs from the screen
            raise RuntimeError("export verification failed: " + "; ".join(problems))
        with self.sv.repo.session() as s:
            batch = s.get(BatchRow, batch_id)
            batch.locked, batch.status, batch.exported_at = True, "exported", now()
            s.add(batch)
            self.audit(
                s,
                user,
                "exported",
                batch_id,
                rows=len(subs),
                forced_with=len(blocked),
            )
            s.commit()
        return data


class ExportBlocked(Exception):
    def __init__(self, fields: list[tuple[str, str]]):
        super().__init__(f"{len(fields)} fields still need review")
        self.fields = fields
