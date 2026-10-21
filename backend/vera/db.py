"""Database tables (spec 14.1). SQLModel, so SQLite now and Postgres later.

A submission row stores the whole `Submission` (spec 14) as JSON: the
contract is the source of truth, the columns next to it are for filtering.
`audit_log` is append-only: the code never updates or deletes it.
"""

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def now() -> datetime:
    return datetime.now(timezone.utc)


class BatchRow(SQLModel, table=True):
    __tablename__ = "batches"
    id: str = Field(primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=now)
    # Dates are judged against this, not today (spec 3.3).
    ref_date: date
    status: str = "new"  # new | processing | done | exported
    locked: bool = False
    exported_at: datetime | None = None
    # Made-up history from `make seed-demo`; shown with a "נתוני דוגמה" badge.
    demo: bool = False


class DocumentRow(SQLModel, table=True):
    __tablename__ = "documents"
    id: str = Field(primary_key=True)
    batch_id: str = Field(foreign_key="batches.id", index=True)
    filename: str
    kind: str  # pdf | jpeg | png | heic
    sha256: str
    storage_key: str
    page_count: int = 0
    seq: int = 0  # upload order in the batch; rows are shown in this order


class PageRow(SQLModel, table=True):
    __tablename__ = "pages"
    id: str = Field(primary_key=True)
    batch_id: str = Field(foreign_key="batches.id", index=True)
    document_id: str = Field(foreign_key="documents.id")
    page_no: int  # 1-based within the document
    status: str = "queued"  # queued | processing | done | failed
    error: str | None = None
    sha256: str | None = None
    rotation: int = 0
    image: str | None = None  # path under the batch work dir
    width: int = 0
    height: int = 0
    barcodes: list = Field(default_factory=list, sa_column=Column(JSON))
    vlm: dict = Field(default_factory=dict, sa_column=Column(JSON))
    vlm_escalated: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # split | read | verify (intake stage strip); set while processing.
    stage: str | None = None
    # e.g. {"sharpness": 41.2, "warnings": ["blur"]}
    quality: dict = Field(default_factory=dict, sa_column=Column(JSON))


class SubmissionRow(SQLModel, table=True):
    __tablename__ = "submissions"
    id: str = Field(primary_key=True)
    batch_id: str = Field(foreign_key="batches.id", index=True)
    row_status: str
    position: int = 0  # order in the batch: document, then page
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=now)


class CorrectionRow(SQLModel, table=True):
    __tablename__ = "corrections"
    id: int | None = Field(default=None, primary_key=True)
    submission_id: str = Field(foreign_key="submissions.id", index=True)
    field: str
    user: str
    at: datetime = Field(default_factory=now)
    old_values: list = Field(default_factory=list, sa_column=Column(JSON))
    new_values: list = Field(default_factory=list, sa_column=Column(JSON))
    via: str = "ui"
    action: str = "edit"  # edit | accept


class TubeScanRow(SQLModel, table=True):
    """One tube scanned at the bench (USB scanner types number + Enter)."""

    __tablename__ = "tube_scans"
    id: int | None = Field(default=None, primary_key=True)
    batch_id: str = Field(foreign_key="batches.id", index=True)
    number: str  # normalized: digits/letters only, no leading zeros
    raw: str
    scanned_at: datetime = Field(default_factory=now)
    user: str = "lab"
    submission_id: str | None = None  # None = tube without a form
    slot: int  # 0-based order in the batch; rack = slot // 30
    demo: bool = False


class FollowupRow(SQLModel, table=True):
    """Something a person has to chase: a missing field, an invalid ID, a
    tube without a form. Drafts are written for a human to send."""

    __tablename__ = "followups"
    id: int | None = Field(default=None, primary_key=True)
    kind: str  # missing_field | invalid_id | tube_orphan
    batch_id: str | None = Field(default=None, index=True)
    submission_id: str | None = None
    field: str | None = None
    sender: str | None = None
    title_he: str
    detail_he: str = ""
    status: str = "open"  # open | sent | closed
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
    closed_at: datetime | None = None
    draft: str | None = None
    note: str | None = None
    demo: bool = False


class ShiftNoteRow(SQLModel, table=True):
    """End-of-shift handover: a team note plus a snapshot of what's open."""

    __tablename__ = "shift_notes"
    id: int | None = Field(default=None, primary_key=True)
    at: datetime = Field(default_factory=now)
    author: str
    text: str
    summary: dict = Field(default_factory=dict, sa_column=Column(JSON))
    acked_by: str | None = None
    acked_at: datetime | None = None
    demo: bool = False


class AuditRow(SQLModel, table=True):
    __tablename__ = "audit_log"
    id: int | None = Field(default=None, primary_key=True)
    at: datetime = Field(default_factory=now)
    user: str
    action: str  # batch_created | file_added | field_edited | exported ...
    target: str
    detail: dict = Field(default_factory=dict, sa_column=Column(JSON))
