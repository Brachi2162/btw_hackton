from datetime import date

import pytest

from vera.config import get_settings
from vera.models import Candidate
from vera.normalize import (
    israeli_id_ok,
    norm_central,
    norm_commitment,
    norm_date,
    norm_id,
    parse_date,
)
from vera.pipeline.decide import (
    DecisionConfig,
    decide_field,
    decide_page,
    merge_reads,
)

# Synthetic values only, never from the sample forms (iron rule 5).
REF = date(2026, 9, 1)
PAGE = "doc/p01"


@pytest.fixture(scope="module")
def cfg():
    return DecisionConfig.load(get_settings().config_dir)


def vlm(field, value, label=None, legible=True, handwritten=False):
    return Candidate(
        field=field,
        value_raw=value,
        reader="vlm_full",
        page_id=PAGE,
        label_nearby=label,
        legible=legible,
        handwritten=handwritten,
    )


def barcode(text, bbox=(10, 10, 200, 60)):
    return {"text": text, "format": "Code128", "bbox": list(bbox)}


# --- normalization ---


def test_id_checksum_and_padding():
    assert israeli_id_ok("000000018")
    assert norm_id("18").problems == ["validation_failed"]  # too short
    assert norm_id("12345674").value == "012345674"
    assert norm_id("0012345674").value == "012345674"  # surplus leading zero
    assert norm_id("012345675").problems == ["checksum_failed"]


def test_central_and_commitment_rules():
    assert norm_central("512 340").value == "512340"
    assert norm_central("512340-25.8").value == "512340"  # screenshot style
    assert norm_central("51234").problems == ["validation_failed"]
    assert norm_commitment("x 120 044").value == "X120044"
    assert norm_commitment("00120").value == "00120"  # leading zeros kept
    assert norm_commitment("12").problems == ["validation_failed"]


@pytest.mark.parametrize(
    "raw,iso",
    [
        ("25/08/2026", "2026-08-25"),
        ("27-08-2026", "2026-08-27"),
        ("27.8.26", "2026-08-27"),
        ("25.8", "2026-08-25"),
        ("31 -08- 2026", "2026-08-31"),
    ],
)
def test_dates_day_first(raw, iso):
    assert parse_date(raw, REF).isoformat() == iso


def test_date_without_year_never_lands_in_the_future():
    assert parse_date("30.12", REF).isoformat() == "2025-12-30"


def test_date_range_is_against_reference_not_today():
    assert norm_date("10/08/2026", REF, 120).problems == []
    assert norm_date("10/09/2026", REF, 120).problems == ["date_out_of_range"]
    assert norm_date("10/01/2026", REF, 120).problems == ["date_out_of_range"]


# --- decision ---


def test_barcode_plus_vlm_with_label_is_green(cfg):
    fr = decide_field(
        "central_numbers",
        [vlm("central_numbers", "512340", label="מרכזי")],
        [barcode("512340")],
        PAGE,
        cfg,
        REF,
    )
    assert fr.status == "verified" and fr.values == ["512340"]
    assert {s.reader for s in fr.sources} == {"vlm_full", "barcode"}
    assert any(s.bbox for s in fr.sources)


def test_agreement_without_the_label_is_not_green(cfg):
    # The barcode proves the digits, not which field they belong to.
    fr = decide_field(
        "central_numbers",
        [vlm("central_numbers", "512340")],
        [barcode("512340")],
        PAGE,
        cfg,
        REF,
    )
    assert fr.status == "likely"


def test_vlm_alone_is_yellow(cfg):
    fr = decide_field(
        "central_numbers",
        [vlm("central_numbers", "512340", label="מרכזי")],
        [],
        PAGE,
        cfg,
        REF,
    )
    assert fr.status == "likely" and "source_single" in fr.reasons


def test_decoy_label_goes_to_review_even_with_barcode(cfg):
    fr = decide_field(
        "id_number",
        [vlm("id_number", "12345674", label="מקרה")],
        [barcode("12345674")],
        PAGE,
        cfg,
        REF,
    )
    assert fr.status == "review" and "decoy_proximity" in fr.reasons


def test_illegible_and_bad_checksum_go_to_review(cfg):
    fr = decide_field("id_number", [vlm("id_number", "012345675")], [], PAGE, cfg, REF)
    assert fr.status == "review" and "checksum_failed" in fr.reasons
    fr = decide_field(
        "first_name", [vlm("first_name", "דנה", legible=False)], [], PAGE, cfg, REF
    )
    assert fr.status == "review" and "illegible" in fr.reasons


def test_nothing_found_is_missing(cfg):
    fr = decide_field("receipt_date", [], [], PAGE, cfg, REF)
    assert fr.status == "missing" and fr.values == []


def test_unlabeled_numbers_dropped_when_labeled_ones_exist(cfg):
    fr = decide_field(
        "central_numbers",
        [
            vlm("central_numbers", "512340", label="מרכזי"),
            vlm("central_numbers", "512341", label="מרכזי"),
            vlm("central_numbers", "700100"),
        ],
        [],
        PAGE,
        cfg,
        REF,
    )
    assert fr.values == ["512340", "512341"] and "700100" in fr.alternatives


def test_two_close_values_for_one_field_go_to_review(cfg):
    fr = decide_field(
        "receipt_date",
        [vlm("receipt_date", "20/08/2026"), vlm("receipt_date", "21/08/2026")],
        [],
        PAGE,
        cfg,
        REF,
    )
    assert fr.status == "review" and "multiple_candidates" in fr.reasons


def test_receipt_before_form_date_is_a_conflict(cfg):
    fr = decide_field(
        "receipt_date",
        [vlm("receipt_date", "10/08/2026", label="התקבל")],
        [],
        PAGE,
        cfg,
        REF,
        aux_dates={"form_date": [date(2026, 8, 12)]},
    )
    assert fr.status == "review" and "date_conflict" in fr.reasons


def test_handwriting_beats_print_and_father_is_a_decoy(cfg):
    fr = decide_field(
        "first_name",
        [vlm("first_name", "דנה"), vlm("first_name", "רות", handwritten=True)],
        [],
        PAGE,
        cfg,
        REF,
    )
    assert fr.values == ["רות"] and "דנה" in fr.alternatives
    fr = decide_field(
        "first_name", [vlm("first_name", "משה", label="אב:")], [], PAGE, cfg, REF
    )
    assert fr.status == "review" and "decoy_proximity" in fr.reasons


def test_page_commitment_never_equals_central_and_row_is_worst(cfg):
    # The same number offered for two fields is a conflict, even from one model.
    page = {
        "page_kind": "form",
        "sender_guess": "כללית",
        "candidates": [
            vlm("central_numbers", "512340", label="מרכזי").model_dump(),
            vlm("commitment_numbers", "512340").model_dump(),
            vlm("commitment_numbers", "88120", label="התחייבות").model_dump(),
        ],
    }
    sub = decide_page(PAGE, "b1", page, [barcode("512340")], cfg, REF)
    assert sub.fields["central_numbers"].status == "review"
    assert "disagree_sources" in sub.fields["central_numbers"].reasons
    assert sub.fields["commitment_numbers"].values == ["88120"]
    assert sub.fields["sender"].values == ["כללית"]
    assert sub.fields["first_name"].status == "missing"
    assert sub.row_status == "review"  # the worst field wins


def test_unreadable_page_puts_every_field_in_review(cfg):
    sub = decide_page(PAGE, "b1", {"error": "VlmInvalid"}, [], cfg, REF)
    assert sub.row_status == "review"
    assert all(f.reasons == ["vlm_invalid"] for f in sub.fields.values())


# --- escalation (a second, different model) ---


def esc(field, value, label=None, legible=True):
    c = vlm(field, value, label=label, legible=legible)
    return c.model_copy(update={"reader": "vlm_escalated"})


def test_two_different_models_agreeing_are_two_sources(cfg):
    fr = decide_field(
        "receipt_date",
        [
            vlm("receipt_date", "20/08/2026", label="התקבל"),
            esc("receipt_date", "20.8.26", label="התקבל"),
        ],
        [],
        PAGE,
        cfg,
        REF,
    )
    assert fr.status == "verified" and fr.values == ["2026-08-20"]


def test_two_reads_of_the_same_model_are_one_source(cfg):
    crop = vlm("receipt_date", "20.8.26", label="התקבל").model_copy(
        update={"reader": "vlm_crop"}
    )
    fr = decide_field(
        "receipt_date",
        [vlm("receipt_date", "20/08/2026", label="התקבל"), crop],
        [],
        PAGE,
        cfg,
        REF,
    )
    assert fr.status == "likely" and "sources_dependent" in fr.reasons


def test_merge_reads_keeps_both_models_and_survives_a_failed_base():
    base = {"page_kind": "form", "sender_guess": "a", "candidates": [{"x": 1}]}
    extra = {
        "page_kind": "form",
        "sender_guess": "b",
        "model": "m2",
        "candidates": [{"y": 2}],
    }
    m = merge_reads(base, extra)
    assert m["candidates"] == [{"x": 1}, {"y": 2}]
    assert m["escalated_sender_guess"] == "b" and m["sender_guess"] == "a"
    m = merge_reads({"error": "VlmInvalid"}, extra)
    assert m["candidates"] == [{"y": 2}] and "error" not in m
    assert merge_reads(base, None) is base


def test_screenshot_date_next_to_central_becomes_receipt_date(cfg):
    page = {
        "page_kind": "screenshot",
        "candidates": [
            vlm("central_numbers", "512340-25.8", label="מרכזי").model_dump()
        ],
    }
    sub = decide_page(PAGE, "b1", page, [], cfg, REF)
    assert sub.fields["central_numbers"].values == ["512340"]
    assert sub.fields["receipt_date"].values == ["2026-08-25"]


def test_parent_organization_sender_is_never_green(cfg):
    page = {
        "page_kind": "form",
        "sender_guess": "כללית",
        "escalated_sender_guess": "כללית",
        "candidates": [],
    }
    sub = decide_page(PAGE, "b1", page, [], cfg, REF)
    assert sub.fields["sender"].values == ["כללית"]
    assert sub.fields["sender"].status == "likely"


def test_models_disagreeing_on_the_field_send_it_to_review(cfg):
    # A 6-digit commitment looks like a central number (spec 3.2).
    page = {
        "page_kind": "form",
        "candidates": [
            vlm("central_numbers", "512340", label="מרכזי").model_dump(),
            vlm("central_numbers", "700100", label="מרכזי").model_dump(),
            esc("central_numbers", "512340", label="מרכזי").model_dump(),
            esc("commitment_numbers", "700100").model_dump(),
        ],
    }
    sub = decide_page(
        PAGE, "b1", page, [barcode("512340"), barcode("700100")], cfg, REF
    )
    central = sub.fields["central_numbers"]
    assert central.status == "review" and "disagree_sources" in central.reasons
