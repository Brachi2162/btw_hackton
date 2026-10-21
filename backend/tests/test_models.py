import pytest
from pydantic import ValidationError

from vera.models import Candidate, FieldResult, Submission, worst_status


def test_worst_status_orders_review_first():
    assert worst_status(["verified", "likely", "review"]) == "review"
    assert worst_status(["verified", "human"]) == "human"
    assert worst_status(["verified", "missing"]) == "missing"
    assert worst_status([]) == "missing"


def test_unknown_reason_code_is_rejected():
    with pytest.raises(ValidationError):
        FieldResult(
            field="id_number", values=[], status="missing", score=0, reasons=["nope"]
        )


def test_candidate_keeps_leading_zeros_as_text():
    c = Candidate(
        field="commitment_numbers", value_raw="000123", reader="barcode", page_id="p"
    )
    assert c.value_raw == "000123"


def test_submission_roundtrips_through_json():
    fr = FieldResult(
        field="central_numbers",
        values=["100200"],
        status="verified",
        score=1.7,
        reasons=[],
    )
    s = Submission(
        id="s1",
        batch_id="b1",
        page_ids=["p1"],
        profile_id="_generic",
        page_kind="form",
        fields={"central_numbers": fr},
        row_status="verified",
    )
    assert Submission.model_validate_json(s.model_dump_json()) == s
