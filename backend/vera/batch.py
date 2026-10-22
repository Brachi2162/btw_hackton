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
from vera.models import Correction, FieldResult, Submission, worst_status
from vera.normalize import (
    norm_central,
    norm_commitment,
    norm_date,
    norm_id,
    norm_name,
)
from vera.pipeline.decide import FIELDS, MULTI, DecisionConfig, decide_page
from vera.pipeline.decide import merge_reads
from vera.pipeline.ingest import UnsupportedFileError, pdf_page_count, sniff_kind
from vera.pipeline.process import (
    crop_region,
    needs_crop,
    needs_escalation,
    prepare_page,
    read_crop,
    read_vlm,
)
from vera.pipeline.vlm import (
    VlmCache,
    VlmConfig,
    make_reader,
    render_prompt,
    sender_names,
)
from vera.services import Services

log = logging.getLogger(__name__)

EXT = {"pdf": "pdf", "jpeg": "jpg", "png": "png", "heic": "heic"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # spec 16: bound work per upload
# Fields where one confirmation is offered to the rest of the batch.
PROPAGATE_FIELDS = {"receipt_date"}


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


class BatchService:
    def __init__(self, services: Services, events: EventBus | None = None):
        self.sv = services
        self.s: Settings = services.settings
        self.events = events or EventBus()
        self.decision = DecisionConfig.load(self.s.config_dir)
        self._readers: Readers | None = None
        self.dpi, self.orient = 300, True  # tests turn these down

    # --- helpers ---

    def work_dir(self, batch_id: str) -> Path:
        return self.s.data_dir / "work" / batch_id

    def readers(self) -> Readers:
        if self._readers is None:
            cfg = VlmConfig.load(self.s.config_dir)
            cache = VlmCache(self.s.data_dir / "cache" / "vlm")
            key = self.s.anthropic_api_key
            self._readers = Readers(
                full=make_reader(self.s.vlm_mode, cfg, cache, key),
                escalated=make_reader(
                    self.s.vlm_mode, cfg, cache, key, model=cfg.escalation_model
                ),
                prompt=render_prompt(
                    self.s.prompts_dir,
                    cfg.prompt,
                    cfg.commitment_aliases,
                    sender_names(self.s.config_dir),
                ),
                crop=(
                    make_reader(self.s.vlm_mode, cfg, cache, key, model=cfg.crop_model)
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
            )
        return self._readers

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
            raise UnsupportedFileError("file too large (max 50MB)")
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
            count = pdf_page_count(src) if kind == "pdf" else 1
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
            jobs = [(p.id, d.id, d.kind, p.page_no) for p, d in pages]

        sem = asyncio.Semaphore(max(1, self.s.max_concurrent_jobs))

        async def one(job):
            async with sem:
                await self._process_page(batch_id, ref, *job)

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
            self._stage(batch_id, page_id, doc_id, "verify")
            sub = decide_page(
                page_id, batch_id, vlm, prep["barcodes"], self.decision, ref
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
                )
                self._set_page(page_id, vlm_crops=crop)
                sub = decide_page(
                    page_id,
                    batch_id,
                    merge_reads(vlm, esc, crop),
                    prep["barcodes"],
                    self.decision,
                    ref,
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
            self._set_page(
                page_id, status="failed", error=f"{type(exc).__name__}: {exc}"
            )
            self.events.publish(
                batch_id, "warning", {"page_id": page_id, "error": type(exc).__name__}
            )

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
            "fields": {
                f: {"values": fr.values, "status": fr.status}
                for f, fr in sub.fields.items()
            },
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

    # --- "one fix that fixes everything" ---

    def propagation(self, sub: Submission, field: str) -> dict | None:
        """After a human confirms a value: other forms in the batch whose
        same field is `likely` with the same value (e.g. one receipt-date
        stamp on the whole morning's pile)."""
        if field not in PROPAGATE_FIELDS:
            return None
        values = sub.fields[field].values
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
            "count": len(ids),
            "submission_ids": ids,
        }

    def propagate(
        self, batch_id: str, field: str, value: str, ids: list[str], user: str
    ) -> dict:
        """Apply a confirmed value to forms that still hold it as `likely`.
        Each one is a normal correction (source human, in `corrections`)."""
        applied, skipped = [], []
        for sid in ids:
            try:
                sub = self.submission(sid)
            except KeyError:
                skipped.append(sid)
                continue
            fr = sub.fields.get(field)
            if (
                sub.batch_id != batch_id
                or fr is None
                or fr.status != "likely"
                or fr.values != [value]
            ):
                skipped.append(sid)  # changed since the suggestion was made
                continue
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
