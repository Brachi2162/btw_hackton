"""The data contract (spec 14): every field comes back in the same shape,
whatever read it. Engine, API, frontend and eval all speak these models.

Changing this file means updating the spec, the frontend and eval too, and
recording a decision in docs/DECISIONS.md.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Spec 9.5. Explanations live in config/reasons.he.yaml, keyed by these codes.
ReasonCode = Literal[
    "source_single",
    "sources_dependent",
    "disagree_sources",
    "multiple_candidates",
    "validation_failed",
    "checksum_failed",
    "date_out_of_range",
    "date_year_anomaly",
    "date_conflict",
    "sequence_outlier",
    "low_quality",
    "unknown_form",
    "handwritten_only",
    "decoy_proximity",
    "name_split_ambiguous",
    "bbox_approx",
    "vlm_invalid",
    "duplicate_in_batch",
    "illegible",  # D-004: a reader saw something but marked it unreadable
]

Reader = Literal["barcode", "ocr", "vlm_full", "vlm_crop", "vlm_escalated", "human"]
Status = Literal["verified", "likely", "review", "missing", "human"]
PageKind = Literal["form", "screenshot", "lab_report", "continuation", "unknown"]

# x1, y1, x2, y2 in pixels of the upright page image.
BBox = tuple[int, int, int, int]


class Candidate(BaseModel):
    """One reading of one value by one reader (spec 9.1), before the decision."""

    field: str
    value_raw: str
    value_norm: str | None = None  # None = failed normalization
    reader: Reader
    model: str | None = None
    page_id: str
    bbox: BBox | None = None
    bbox_approx: bool = False
    evidence_text: str | None = None
    legible: bool = True
    handwritten: bool = False
    label_nearby: str | None = None
    signals: dict[str, float | str | bool] = Field(default_factory=dict)


class Source(BaseModel):
    reader: Reader
    model: str | None = None  # e.g. the VLM model name
    values: list[str]
    page_id: str
    bbox: BBox | None = None
    bbox_approx: bool = False
    evidence_text: str | None = None
    legible: bool = True
    handwritten: bool = False


class Correction(BaseModel):
    user: str
    at: datetime
    old_values: list[str]
    new_values: list[str]
    via: Literal["ui", "assistant"]


class FieldResult(BaseModel):
    field: str  # key from config/fields.yaml
    values: list[str]  # normalized; empty = not found
    status: Status
    score: float  # internal; for calibration and review-queue order
    reasons: list[ReasonCode] = Field(default_factory=list)
    explanation_he: str = ""  # rendered from config/reasons.he.yaml
    sources: list[Source] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)  # rejected, offered 1/2/3
    history: list[Correction] = Field(default_factory=list)


# Worst first: a row's status is the worst of its fields (spec 9.3).
STATUS_ORDER: tuple[Status, ...] = ("review", "missing", "likely", "human", "verified")


def worst_status(statuses: list[Status]) -> Status:
    if not statuses:
        return "missing"
    return min(statuses, key=STATUS_ORDER.index)


class Submission(BaseModel):
    """One commitment form = one row in the results table."""

    id: str
    batch_id: str
    page_ids: list[str]
    profile_id: str
    page_kind: PageKind
    fields: dict[str, FieldResult]
    row_status: Status
    batch_flags: list[ReasonCode] = Field(default_factory=list)
