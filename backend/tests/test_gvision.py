from types import SimpleNamespace

import pytest
from PIL import Image

from vera.normalize import norm_central, norm_name
from vera.pipeline.gvision import VisionOcr, find_value, to_lines
from vera.pipeline.vlm import CacheMiss, VlmInvalid

# Synthetic values only, never from the sample forms (iron rule 5).


def word(text, x, y=10, conf=0.95, brk=None):
    symbols = [{"text": ch} for ch in text]
    if brk:
        symbols[-1]["property"] = {"detectedBreak": {"type": brk}}
    return {
        "symbols": symbols,
        "confidence": conf,
        "boundingBox": {
            "vertices": [
                {"x": x, "y": y},
                {"x": x + 40, "y": y},
                {"x": x + 40, "y": y + 20},
                {"x": x, "y": y + 20},
            ]
        },
    }


RESPONSE = {
    "fullTextAnnotation": {
        "pages": [
            {
                "blocks": [
                    {
                        "paragraphs": [
                            {
                                "words": [
                                    word("מרכזי", 300),
                                    word("512", 200),
                                    word("340", 150, brk="LINE_BREAK"),
                                    word("דוד", 300, y=60, conf=0.7),
                                    word("בן", 250, y=60, brk="EOL_SURE_SPACE"),
                                ]
                            }
                        ]
                    }
                ]
            }
        ]
    }
}


def test_lines_split_on_line_breaks_and_keep_boxes():
    lines = to_lines(RESPONSE, scale=2.0)
    assert [ln["text"] for ln in lines] == ["מרכזי 512 340", "דוד בן"]
    assert lines[0]["words"][1]["bbox"] == [400, 20, 480, 60]
    assert lines[0]["words"][1]["conf"] == 0.95


def test_find_value_joins_split_numbers_and_reversed_names():
    lines = to_lines(RESPONSE)
    hit = find_value(lines, "512340", lambda s: norm_central(s).value)
    assert hit.text == "512 340" and hit.bbox == (150, 10, 240, 30)
    assert hit.line == "מרכזי 512 340"
    name = find_value(lines, "בן דוד", lambda s: norm_name(s).value)
    assert name and name.confidence == 0.7
    assert find_value(lines, "512341", lambda s: norm_central(s).value) is None


class FakeHttp:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def post(self, url, params, json):
        self.calls.append({"url": url, "params": params, "json": json})
        return self.answers.pop(0)


def ok(body):
    return SimpleNamespace(status_code=200, text="", json=lambda: body)


@pytest.fixture
def page():
    return Image.new("RGB", (6000, 4000), "white")


def test_read_is_cached_and_boxes_map_back_to_the_page(tmp_path, page):
    client = FakeHttp(ok({"responses": [RESPONSE]}))
    ocr = VisionOcr(tmp_path, "k", client=client, max_side=3000)
    first = ocr.read(page, "sha-1")
    assert ocr.read(page, "sha-1") == first and len(client.calls) == 1
    req = client.calls[0]["json"]["requests"][0]
    assert req["features"] == [{"type": "DOCUMENT_TEXT_DETECTION"}]
    assert client.calls[0]["params"] == {"key": "k"}
    assert first[0]["words"][1]["bbox"] == [400, 20, 480, 60]  # sent at half size
    assert VisionOcr(tmp_path, mode="replay").read(page, "sha-1") == first


def test_replay_without_cache_and_api_errors(tmp_path, page):
    with pytest.raises(CacheMiss):
        VisionOcr(tmp_path, mode="replay").read(page, "sha-x")
    denied = SimpleNamespace(
        status_code=403,
        text="{}",
        json=lambda: {"error": {"message": "Cloud Vision API has not been used"}},
    )
    with pytest.raises(VlmInvalid, match="not been used"):
        VisionOcr(tmp_path, "k", client=FakeHttp(denied)).read(page, "sha-2")
    inner = ok({"responses": [{"error": {"message": "bad image"}}]})
    with pytest.raises(VlmInvalid, match="bad image"):
        VisionOcr(tmp_path, "k", client=FakeHttp(inner)).read(page, "sha-3")
    assert not list(tmp_path.rglob("*.json"))
