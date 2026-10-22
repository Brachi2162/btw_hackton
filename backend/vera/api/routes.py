"""HTTP API (spec 14.2). A thin layer over vera.batch.BatchService.

Users: until login exists (spec 4, SHOULD), the acting user comes from the
X-User header and defaults to "lab". Every change is written to audit_log.
"""

import asyncio
import io
import json
from datetime import date
from urllib.parse import unquote

from fastapi import APIRouter, File, Header, HTTPException, Query, Request
from fastapi import UploadFile
from fastapi.responses import Response, StreamingResponse
from PIL import Image
from pydantic import BaseModel
from sqlmodel import select

from vera.batch import BatchService, ExportBlocked, ValidationError
from vera.db import BatchRow, DocumentRow, PageRow
from vera.pipeline.ingest import UnsupportedFileError

router = APIRouter(prefix="/api")

# Page quality warnings (batch.page_quality, vera.pipeline.enhance).
QUALITY_HE = {
    "blur": "העמוד מטושטש, כדאי לסרוק שוב",
    "skewed": "הסריקה עקומה; כדאי לבדוק שהערכים נקראו נכון או לסרוק שוב",
    "dark": "הסריקה הייתה חשוכה והובהרה אוטומטית; כדאי לבדוק את הערכים",
}


def svc(request: Request) -> BatchService:
    return request.app.state.batches


def user_of(x_user: str | None) -> str:
    # The UI URL-encodes the header: HTTP headers can't carry Hebrew as is.
    return unquote(x_user or "lab").strip()[:50] or "lab"


# --- batches ---


class BatchIn(BaseModel):
    name: str = "אצווה"
    ref_date: date | None = None  # dates are judged against this (spec 3.3)


@router.post("/batches")
def create_batch(body: BatchIn, request: Request, x_user: str | None = Header(None)):
    b = svc(request).create_batch(body.name, body.ref_date, user_of(x_user))
    return b.model_dump()


@router.get("/batches")
def list_batches(request: Request):
    with svc(request).sv.repo.session() as s:
        rows = s.exec(select(BatchRow).order_by(BatchRow.created_at.desc())).all()
        return [r.model_dump() for r in rows]


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, request: Request):
    service = svc(request)
    with service.sv.repo.session() as s:
        batch = s.get(BatchRow, batch_id)
        if batch is None:
            raise HTTPException(404, "batch not found")
        out = batch.model_dump()
    out["counts"] = service.counts(batch_id)
    out["submissions"] = [service.summary(x) for x in service.submissions(batch_id)]
    out.update(_progress(service, batch_id))
    return out


def _progress(service: BatchService, batch_id: str) -> dict:
    """Per-file progress for the intake screen, and blur warnings."""
    with service.sv.repo.session() as s:
        docs = s.exec(
            select(DocumentRow)
            .where(DocumentRow.batch_id == batch_id)
            .order_by(DocumentRow.seq)
        ).all()
        pages = s.exec(select(PageRow).where(PageRow.batch_id == batch_id)).all()
    by_doc: dict[str, list[PageRow]] = {}
    for p in pages:
        by_doc.setdefault(p.document_id, []).append(p)
    documents, warnings = [], []
    for d in docs:
        ps = by_doc.get(d.id, [])
        documents.append(
            {
                "id": d.id,
                "filename": d.filename,
                "pages": d.page_count,
                "done": sum(p.status == "done" for p in ps),
                "failed": sum(p.status == "failed" for p in ps),
                "stages": [p.stage for p in ps if p.status == "processing"],
                # distinct reasons, so the screen can say why pages failed
                "errors": sorted({p.error for p in ps if p.error})[:3],
            }
        )
        for p in ps:
            q = p.quality or {}
            found = [QUALITY_HE[w] for w in q.get("warnings", []) if w in QUALITY_HE]
            if found:
                warnings.append(
                    {
                        "page_id": p.id,
                        "filename": d.filename,
                        "page_no": p.page_no,
                        "sharpness": q.get("sharpness"),
                        "message_he": f"{d.filename} עמ' {p.page_no}: "
                        + "; ".join(found),
                    }
                )
    return {"documents": documents, "quality_warnings": warnings}


@router.post("/batches/{batch_id}/files")
async def upload_files(
    batch_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    x_user: str | None = Header(None),
):
    """Store the files, then process the whole batch in the background.
    Progress arrives on /events."""
    service = svc(request)
    added, rejected = [], []
    for f in files:
        data = await f.read()
        try:
            added.append(
                service.add_file(batch_id, f.filename or "file", data, user_of(x_user))
            )
        except KeyError:
            raise HTTPException(404, "batch not found")
        except PermissionError as e:
            raise HTTPException(409, str(e))
        except UnsupportedFileError as e:
            rejected.append({"filename": f.filename, "error": str(e)})
    job_id = None
    if added:
        job_id = await service.sv.jobs.submit(service.process_batch, batch_id)
    return {"added": added, "rejected": rejected, "job_id": job_id}


@router.post("/batches/{batch_id}/retry-failed")
async def retry_failed(
    batch_id: str, request: Request, x_user: str | None = Header(None)
):
    """Read the failed pages again; pages already read are not touched.
    Progress arrives on /events, as after an upload."""
    service = svc(request)
    try:
        requeued = service.requeue_failed(batch_id, user_of(x_user))
    except KeyError:
        raise HTTPException(404, "batch not found")
    except PermissionError as e:
        raise HTTPException(409, str(e))
    job_id = None
    if requeued:
        job_id = await service.sv.jobs.submit(service.process_batch, batch_id)
    return {"requeued": requeued, "job_id": job_id}


@router.post("/batches/{batch_id}/accept-all")
def accept_all(batch_id: str, request: Request, x_user: str | None = Header(None)):
    """Accept every field with a value that is not green yet; empty fields
    stay empty."""
    try:
        return svc(request).accept_all(batch_id, user_of(x_user))
    except KeyError:
        raise HTTPException(404, "batch not found")
    except PermissionError as e:
        raise HTTPException(409, str(e))


@router.get("/batches/{batch_id}/events")
async def events(batch_id: str, request: Request):
    """SSE: page_done, submission_updated, batch_done, warning."""
    service = svc(request)
    q = service.events.subscribe(batch_id)

    async def stream():
        try:
            yield f"event: hello\ndata: {json.dumps(service.counts(batch_id))}\n\n"
            while not await request.is_disconnected():
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                payload = json.dumps(msg["data"], ensure_ascii=False, default=str)
                yield f"event: {msg['event']}\ndata: {payload}\n\n"
        finally:
            service.events.unsubscribe(batch_id, q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/batches/{batch_id}/review-queue")
def review_queue(batch_id: str, request: Request):
    return svc(request).review_queue(batch_id)


@router.post("/batches/{batch_id}/export")
def export(
    batch_id: str,
    request: Request,
    force: bool = False,
    x_user: str | None = Header(None),
):
    try:
        data = svc(request).export(batch_id, user_of(x_user), force=force)
    except KeyError:
        raise HTTPException(404, "batch not found")
    except ExportBlocked as e:
        raise HTTPException(
            409,
            {
                "message": f"יש {len(e.fields)} שדות כתומים שלא אושרו",
                "fields": [{"submission_id": s, "field": f} for s, f in e.fields],
            },
        )
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="vera-{batch_id}.xlsx"'},
    )


# --- submissions ---


@router.get("/submissions/{sub_id}")
def get_submission(sub_id: str, request: Request):
    try:
        return svc(request).submission(sub_id).model_dump(mode="json")
    except KeyError:
        raise HTTPException(404, "submission not found")


class FieldEdit(BaseModel):
    values: list[str]
    force: bool = False  # save even if the checks fail (logged)


def _correct(request, sub_id, field, values, x_user, force=False):
    service = svc(request)
    try:
        sub = service.correct(sub_id, field, values, user_of(x_user), force)
    except KeyError:
        raise HTTPException(404, "submission not found")
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except ValidationError as e:
        raise HTTPException(422, str(e))
    out = sub.model_dump(mode="json")
    # "One fix that fixes everything": offer the same value to other forms.
    out["propagation"] = service.propagation(sub, field)
    return out


@router.patch("/submissions/{sub_id}/fields/{field}")
def edit_field(
    sub_id: str,
    field: str,
    body: FieldEdit,
    request: Request,
    x_user: str | None = Header(None),
):
    return _correct(request, sub_id, field, body.values, x_user, body.force)


@router.post("/submissions/{sub_id}/fields/{field}/accept")
def accept_field(
    sub_id: str, field: str, request: Request, x_user: str | None = Header(None)
):
    return _correct(request, sub_id, field, None, x_user)


@router.get("/search")
def search(q: str, request: Request):
    return svc(request).search(q)


# --- pages ---


@router.get("/pages/{page_id}/image")
def page_image(
    page_id: str,
    request: Request,
    crop: str | None = Query(None, description="x1,y1,x2,y2 in page pixels"),
    scale: float = Query(1.0, gt=0, le=4),
    max_side: int = Query(1600, ge=100, le=4000),
):
    """The upright page (or a crop of it) as JPEG, for the evidence panel."""
    service = svc(request)
    with service.sv.repo.session() as s:
        page = s.get(PageRow, page_id)
        if page is None or not page.image:
            raise HTTPException(404, "page not ready")
        path = service.work_dir(page.batch_id) / page.image
    with Image.open(path) as img:
        img = img.convert("RGB")
        if crop:
            try:
                x1, y1, x2, y2 = (int(float(v)) for v in crop.split(","))
            except ValueError:
                raise HTTPException(422, "crop must be x1,y1,x2,y2")
            box = (max(0, x1), max(0, y1), min(img.width, x2), min(img.height, y2))
            if box[2] <= box[0] or box[3] <= box[1]:
                raise HTTPException(422, "empty crop")
            img = img.crop(box)
            if scale != 1:
                img = img.resize(
                    (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                    Image.Resampling.LANCZOS,
                )
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
    return Response(
        buf.getvalue(),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/pages/{page_id}")
def get_page(page_id: str, request: Request):
    with svc(request).sv.repo.session() as s:
        page = s.get(PageRow, page_id)
        if page is None:
            raise HTTPException(404, "page not found")
        out = page.model_dump(exclude={"vlm", "vlm_escalated"})
        out["barcodes"] = page.barcodes
        return out
