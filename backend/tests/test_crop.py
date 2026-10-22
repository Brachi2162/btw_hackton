"""Crop reads of the patient area (D-005, D-012): letter-by-letter reading,
the zoom into the name line, and a check read by another provider.
Synthetic names and numbers only, never from the sample forms (iron rule 5)."""

from types import SimpleNamespace

import pytest
from PIL import Image

from vera.config import get_settings
from vera.pipeline.process import read_crop
from vera.pipeline.vlm import (
    ClaudeReader,
    CropReading,
    CropReadingV1,
    VlmCache,
    VlmConfig,
    crop_image,
    crop_schema,
    crop_to_candidates,
    zoom_box,
)

W, H = 2000, 3000


def crop_reading(first="דנה", last="כהן", id_="12345674", box=(0, 0, 0, 0), **kw):
    return CropReading(
        name_line=f"{first} {last}",
        first_name_letters=kw.pop("first_letters", list(first)),
        first_name=first,
        last_name_letters=kw.pop("last_letters", list(last)),
        last_name=last,
        id_number=id_,
        first_name_legible=kw.pop("first_legible", True),
        last_name_legible=kw.pop("last_legible", True),
        id_legible=True,
        box_left=box[0],
        box_top=box[1],
        box_right=box[2],
        box_bottom=box[3],
    )


def response(parsed):
    return SimpleNamespace(
        parsed_output=parsed,
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        _request_id="req_test",
    )


class FakeClient:
    """Answers in order and remembers the size of every image it was sent."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.answers.pop(0)


@pytest.fixture
def cfg():
    return VlmConfig.load(get_settings().config_dir)


@pytest.fixture
def page_png(tmp_path):
    path = tmp_path / "page.png"
    Image.new("RGB", (W, H), "white").save(path)
    return path


def run_crop(cfg, tmp_path, page_png, client, **kw):
    reader = ClaudeReader(cfg, VlmCache(tmp_path / "cache"), client=client, model="m")
    return read_crop(
        reader, page_png, "sha", "prompt", "crop_patient.v2", 1568, "doc/p01",
        "top-right", **kw,
    )  # fmt: skip


def test_small_crop_is_enlarged_big_one_left_alone():
    img = Image.new("RGB", (W, H))
    assert crop_image(img, (0, 0, 400, 100), 1568).size == (1200, 300)  # 3x cap
    assert crop_image(img, (0, 0, 1000, 200), 1568).size == (1568, 314)
    assert crop_image(img, (0, 0, 1800, 1800), 1568).size == (1800, 1800)


def test_zoom_box_maps_thousandths_to_page_pixels_with_margins():
    crop = (1000, 0, 2000, 1000)
    tight = zoom_box(crop_reading(box=(100, 400, 900, 500)), crop)
    x0, y0, x1, y1 = tight
    assert 1000 <= x0 < 1100 and 1900 < x1 <= 2000  # inside the crop, wider
    assert y0 < 400 and y1 > 500 and y1 - y0 >= 160
    # No box, a broken box, or one almost the whole crop: no zoom.
    assert zoom_box(crop_reading(), crop) is None
    assert zoom_box(crop_reading(box=(500, 500, 400, 600)), crop) is None
    assert zoom_box(crop_reading(box=(0, 0, 1000, 900)), crop) is None


def test_letters_that_dont_match_the_name_are_not_a_clear_read():
    r = SimpleNamespace(reading=crop_reading(first_letters=["ד", "נ"]), model="m")
    cands = {c.field: c for c in crop_to_candidates(r, "doc/p01", (0, 0, 10, 10))}
    assert not cands["first_name"].legible
    assert cands["last_name"].legible
    assert cands["first_name"].evidence_text == "דנה כהן"


def test_crop_zooms_into_the_line_it_found(tmp_path, cfg, page_png):
    wide = crop_reading(first="דנ", box=(100, 300, 700, 400))
    client = FakeClient(response(wide), response(crop_reading()))
    out = run_crop(cfg, tmp_path, page_png, client)
    assert len(client.calls) == 2
    assert out["located_in"] and out["box"] != out["located_in"]
    names = {c["field"]: c["value_raw"] for c in out["candidates"]}
    assert names["first_name"] == "דנה"  # the zoomed read, not the wide one
    assert out["usage"]["input_tokens"] == 2000


def test_no_box_means_one_read(tmp_path, cfg, page_png):
    client = FakeClient(response(crop_reading()))
    out = run_crop(cfg, tmp_path, page_png, client)
    assert len(client.calls) == 1 and out["located_in"] is None


def test_check_reader_reads_the_given_box_once(tmp_path, cfg, page_png):
    client = FakeClient(response(crop_reading(box=(100, 300, 700, 400))))
    box = (1200, 500, 1900, 700)
    out = run_crop(
        cfg, tmp_path, page_png, client, box=box, reader_name="vlm_crop_check"
    )
    assert len(client.calls) == 1  # no zoom on a given box
    assert out["box"] == list(box)
    assert {c["reader"] for c in out["candidates"]} == {"vlm_crop_check"}


def test_v1_answers_keep_their_schema_and_never_zoom():
    assert crop_schema("crop_patient.v1") is CropReadingV1
    assert crop_schema("crop_patient.v2") is CropReading
    v1 = CropReadingV1(
        first_name="דנה", last_name="כהן", id_number="", name_legible=False,
        id_legible=True,
    )  # fmt: skip
    assert zoom_box(v1, (0, 0, 1000, 1000)) is None
    r = SimpleNamespace(reading=v1, model="m")
    cands = crop_to_candidates(r, "doc/p01", (0, 0, 10, 10))
    assert [c.field for c in cands] == ["first_name", "last_name"]
    assert not any(c.legible for c in cands)
