"""HTTP API for the screens around review: evidence ("why?"), one-fix
propagation, tubes, follow-ups, reports, handover and the assistant.
Thin like routes.py: the logic lives in the vera.* modules."""

from datetime import date

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from vera import followups as fu
from vera import handover as ho
from vera import inbox, reports, tubes
from vera.api.routes import svc, user_of
from vera.batch import ValidationError
from vera.evidence import evidence

router = APIRouter(prefix="/api")


def assistant(request: Request):
    return request.app.state.assistant


# --- evidence and propagation ---


@router.get("/submissions/{sub_id}/fields/{field}/evidence")
def field_evidence(sub_id: str, field: str, request: Request):
    service = svc(request)
    try:
        sub = service.submission(sub_id)
    except KeyError:
        raise HTTPException(404, "submission not found")
    if field not in sub.fields:
        raise HTTPException(404, "field not found")
    return evidence(sub, field, service.decision.reasons_he)


class PropagateIn(BaseModel):
    field: str
    value: str
    submission_ids: list[str]


@router.post("/batches/{batch_id}/propagate")
def propagate(
    batch_id: str,
    body: PropagateIn,
    request: Request,
    x_user: str | None = Header(None),
):
    try:
        return svc(request).propagate(
            batch_id, body.field, body.value, body.submission_ids, user_of(x_user)
        )
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except ValidationError as e:
        raise HTTPException(422, str(e))


# --- import from the scanner folder ---


@router.get("/inbox")
def inbox_preview(request: Request, path: str = "", today: bool = False):
    try:
        return inbox.scan(svc(request), path, today_only=today)
    except inbox.InboxError as e:
        raise HTTPException(422, str(e))


class ImportIn(BaseModel):
    path: str = ""
    today_only: bool = False


@router.post("/inbox/import")
async def inbox_import(
    body: ImportIn, request: Request, x_user: str | None = Header(None)
):
    """Takes only the relevant files from the folder; same processing as an
    upload, without sending files through the browser."""
    service = svc(request)
    try:
        out = inbox.import_folder(service, body.path, user_of(x_user), body.today_only)
    except inbox.InboxError as e:
        raise HTTPException(422, str(e))
    if out["added"]:
        out["job_id"] = await service.sv.jobs.submit(
            service.process_batch, out["batch_id"]
        )
    return out


# --- tubes ---


class ScanIn(BaseModel):
    number: str


@router.post("/batches/{batch_id}/tubes/scan")
def tube_scan(
    batch_id: str, body: ScanIn, request: Request, x_user: str | None = Header(None)
):
    try:
        return tubes.scan(svc(request), batch_id, body.number, user_of(x_user))
    except KeyError:
        raise HTTPException(404, "batch not found")
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/batches/{batch_id}/tubes")
def tube_status(batch_id: str, request: Request):
    try:
        return tubes.status(svc(request), batch_id)
    except KeyError:
        raise HTTPException(404, "batch not found")


# --- follow-ups ---


@router.get("/followups")
def followups(request: Request, status: str | None = None, demo: bool = True):
    return fu.list_followups(svc(request), status, demo)


class FollowupPatch(BaseModel):
    status: str | None = None
    note: str | None = None
    draft: str | None = None


@router.patch("/followups/{fid}")
def followup_patch(
    fid: int, body: FollowupPatch, request: Request, x_user: str | None = Header(None)
):
    try:
        return fu.update(
            svc(request), fid, user_of(x_user), **body.model_dump(exclude_none=True)
        )
    except KeyError:
        raise HTTPException(404, "follow-up not found")
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/followups/{fid}/draft")
def followup_draft(fid: int, request: Request, x_user: str | None = Header(None)):
    """Drafts a letter. Never sends: the person copies, edits and sends it."""
    try:
        return fu.draft(svc(request), fid, assistant(request), user_of(x_user))
    except KeyError:
        raise HTTPException(404, "follow-up not found")


# --- reports ---


@router.get("/reports/weekly")
def weekly(request: Request, end: date | None = None, demo: bool = True):
    return reports.weekly(svc(request), end, demo)


class LetterIn(BaseModel):
    sender: str
    end: date | None = None


@router.post("/reports/letter")
def letter(body: LetterIn, request: Request):
    try:
        return reports.letter(svc(request), body.sender, body.end)
    except KeyError:
        raise HTTPException(404, "no forms from this sender this week")


# --- overview and handover ---


@router.get("/overview")
def overview(request: Request):
    return ho.overview(svc(request))


@router.get("/handover")
def handover(request: Request):
    return ho.handover(svc(request))


class NoteIn(BaseModel):
    text: str


@router.post("/handover/note")
def handover_note(body: NoteIn, request: Request, x_user: str | None = Header(None)):
    try:
        return ho.add_note(svc(request), user_of(x_user), body.text)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/handover/{note_id}/ack")
def handover_ack(note_id: int, request: Request, x_user: str | None = Header(None)):
    try:
        return ho.ack(svc(request), note_id, user_of(x_user))
    except KeyError:
        raise HTTPException(404, "handover not found")


# --- assistant ---


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatIn(BaseModel):
    messages: list[ChatMessage]


@router.post("/assistant/chat")
def chat(body: ChatIn, request: Request):
    try:
        return assistant(request).chat([m.model_dump() for m in body.messages])
    except ValueError as e:
        raise HTTPException(422, str(e))


class ConfirmIn(BaseModel):
    tool: str
    args: dict


@router.post("/assistant/confirm")
def confirm(body: ConfirmIn, request: Request, x_user: str | None = Header(None)):
    """Runs a write action the user confirmed on a card from /chat."""
    try:
        result = assistant(request).confirm(body.tool, body.args, user_of(x_user))
    except KeyError:
        raise HTTPException(404, "not found")
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except (ValidationError, ValueError) as e:
        raise HTTPException(422, str(e))
    return {"ok": True, "result": result}
