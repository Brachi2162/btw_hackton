"""Form-type profiles (D-019). Synthetic profiles and values only, never
the real profiles' patterns or values from the sample forms (iron rule 5)."""

from datetime import date

import pytest

from vera.config import get_settings
from vera.models import Candidate
from vera.pipeline.decide import DecisionConfig, decide_field
from vera.profiles import Profile, load_profiles, pick

REF = date(2026, 9, 1)
PAGE = "doc/p01"
FORM = Profile(
    id="t",
    name="טופס בדיקה",
    senders=["acme"],
    commitment_regex=r"^Q\d{3}$",
    commitment_regions=["bottom-left"],
)


@pytest.fixture(scope="module")
def cfg():
    return DecisionConfig.load(get_settings().config_dir)


def cand(value, reader="vlm_full", region="bottom-left"):
    return Candidate(
        field="commitment_numbers",
        value_raw=value,
        reader=reader,
        page_id=PAGE,
        signals={"region": region},
    )


def commitment(cfg, cands, profile=FORM):
    return decide_field(
        "commitment_numbers", cands, [], PAGE, cfg, REF, profile=profile
    )


def test_profiles_in_config_load_and_are_picked_by_sender_and_kind():
    profiles = load_profiles(get_settings().config_dir)
    assert profiles and len({p.id for p in profiles}) == len(profiles)
    assert pick([FORM], "acme", "form") is FORM
    assert pick([FORM], "acme", "screenshot") is None
    assert pick([FORM], "other", "form") is None


def test_a_bad_pattern_fails_at_load():
    with pytest.raises(Exception):
        Profile(id="x", name="x", senders=[], commitment_regex="(")


def test_the_form_pattern_rejects_other_numbers(cfg):
    fr = commitment(cfg, [cand("1234567")])
    assert fr.status == "review" and "validation_failed" in fr.reasons


def test_place_lifts_label_missing_but_one_source_stays_yellow(cfg):
    generic = commitment(cfg, [cand("Q123")], profile=None)
    assert "label_missing" in generic.reasons
    fr = commitment(cfg, [cand("Q123")])
    assert "label_missing" not in fr.reasons
    assert fr.status == "likely"  # a pattern can't catch a misread digit


def test_place_and_two_sources_is_green(cfg):
    fr = commitment(cfg, [cand("Q123"), cand("Q123", reader="vlm_escalated")])
    assert fr.status == "verified"


def test_somewhere_else_on_the_page_is_not_the_place(cfg):
    fr = commitment(cfg, [cand("Q123", region="top-right")])
    assert "label_missing" in fr.reasons
