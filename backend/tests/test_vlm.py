import io
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from vera.config import get_settings
from vera.pipeline.vlm import (
    CacheMiss,
    ClaudeReader,
    GeminiReader,
    PageReading,
    ReplayReader,
    VlmCache,
    VlmConfig,
    VlmInvalid,
    cache_key,
    make_reader,
    prepare_image,
    render_prompt,
    sender_names,
    to_candidates,
)

# Synthetic values only, never from the sample forms (iron rule 5).
FIELDS = (
    "first_name last_name id_number central_numbers receipt_date sender "
    "commitment_numbers form_date sample_date"
).split()


def cand(value, region="top-right", **kw):
    return {
        "value_raw": value,
        "legible": True,
        "handwritten": False,
        "label_nearby": kw.pop("label", ""),
        "evidence_text": kw.pop("evidence", ""),
        "region": region,
        **kw,
    }


def reading(**fields) -> PageReading:
    return PageReading.model_validate(
        {
            "page_kind": "form",
            "upright": True,
            "sender_guess": "unknown",
            "page_marker": "",
            "fields": {f: fields.get(f, []) for f in FIELDS},
        }
    )


def response(parsed, stop_reason="end_turn"):
    return SimpleNamespace(
        parsed_output=parsed,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=1500, output_tokens=300),
        _request_id="req_test",
    )


class FakeClient:
    """Stands in for anthropic.Anthropic: returns queued answers in order."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def cfg():
    return VlmConfig.load(get_settings().config_dir)


@pytest.fixture
def page():
    return Image.new("RGB", (2480, 3508), "white")


def schema_error() -> ValidationError:
    try:
        PageReading.model_validate({})
    except ValidationError as e:
        return e


def test_config_and_prompt_render(cfg):
    s = get_settings()
    prompt = render_prompt(
        s.prompts_dir, cfg.prompt, cfg.commitment_aliases, sender_names(s.config_dir)
    )
    assert "{" not in prompt and "}" not in prompt
    assert '"התחייבות"' in prompt
    assert "כללית" in prompt
    assert "none" in prompt  # no profile hints yet


def test_prepare_image_downscales_and_strips_metadata(page):
    data = prepare_image(page, max_side=1800, quality=90)
    out = Image.open(io.BytesIO(data))
    assert max(out.size) == 1800 and out.format == "JPEG"
    assert not out.getexif()


def test_cache_key_depends_on_prompt_and_model(cfg):
    a = cache_key("abc", "prompt", "m1", cfg)
    assert a == cache_key("abc", "prompt", "m1", cfg)
    assert a != cache_key("abc", "prompt 2", "m1", cfg)
    assert a != cache_key("abc", "prompt", "m2", cfg)
    assert a != cache_key("abd", "prompt", "m1", cfg)


def test_live_read_is_cached_then_replayed(tmp_path, cfg, page):
    cache = VlmCache(tmp_path)
    client = FakeClient(response(reading(central_numbers=[cand("123456")])))
    live = ClaudeReader(cfg, cache, client=client)

    first = live.read_page(page, "sha-1", "prompt")
    again = live.read_page(page, "sha-1", "prompt")
    assert not first.cached and again.cached
    assert len(client.calls) == 1
    assert client.calls[0]["model"] == cfg.model
    assert client.calls[0]["output_format"] is PageReading
    assert client.calls[0]["output_config"] == {"effort": cfg.effort}
    image = client.calls[0]["messages"][0]["content"][0]
    assert image["source"]["media_type"] == "image/jpeg"

    replay = ReplayReader(cfg, cache).read_page(page, "sha-1", "prompt")
    assert replay.reading == first.reading and replay.usage["input_tokens"] == 1500


def test_replay_never_calls_out(tmp_path, cfg, page):
    with pytest.raises(CacheMiss):
        ReplayReader(cfg, VlmCache(tmp_path)).read_page(page, "sha-x", "prompt")


def test_invalid_answer_is_retried_once(tmp_path, cfg, page):
    client = FakeClient(schema_error(), response(reading()))
    result = ClaudeReader(cfg, VlmCache(tmp_path), client=client).read_page(
        page, "s", "p"
    )
    assert len(client.calls) == 2 and not result.cached


def test_two_invalid_answers_raise_and_cache_nothing(tmp_path, cfg, page):
    client = FakeClient(response(None), response(reading(), stop_reason="max_tokens"))
    reader = ClaudeReader(cfg, VlmCache(tmp_path), client=client)
    with pytest.raises(VlmInvalid):
        reader.read_page(page, "s", "p")
    assert not list(tmp_path.rglob("*.json"))


def test_refusal_is_not_retried(tmp_path, cfg, page):
    client = FakeClient(response(None, stop_reason="refusal"))
    with pytest.raises(VlmInvalid):
        ClaudeReader(cfg, VlmCache(tmp_path), client=client).read_page(page, "s", "p")
    assert len(client.calls) == 1


def test_to_candidates_flattens_and_marks_bbox_approx(tmp_path, cfg, page):
    r = reading(
        central_numbers=[cand("123456", label="מרכזי"), cand("654321")],
        commitment_numbers=[cand("000123", region="bottom-left", handwritten=True)],
        id_number=[cand("  ")],  # blank readings are dropped
    )
    client = FakeClient(response(r))
    result = ClaudeReader(cfg, VlmCache(tmp_path), client=client).read_page(
        page, "s", "p"
    )
    cands = to_candidates(result, "doc/p01")
    assert [(c.field, c.value_raw) for c in cands] == [
        ("central_numbers", "123456"),
        ("central_numbers", "654321"),
        ("commitment_numbers", "000123"),
    ]
    assert all(c.reader == "vlm_full" and c.bbox_approx for c in cands)
    assert cands[0].label_nearby == "מרכזי" and cands[1].label_nearby is None
    assert cands[2].handwritten and cands[2].signals["region"] == "bottom-left"


def gemini(parsed=None, finish="STOP", status=200, text=None):
    body = {
        "candidates": [
            {
                "finishReason": finish,
                "content": {
                    "parts": [
                        {"text": "thinking...", "thought": True},
                        {
                            "text": (
                                text if text is not None else parsed.model_dump_json()
                            )
                        },
                    ]
                },
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 1200,
            "candidatesTokenCount": 200,
            "thoughtsTokenCount": 50,
        },
        "responseId": "resp_test",
    }
    return SimpleNamespace(status_code=status, json=lambda: body)


class FakeHttp:
    """Stands in for httpx.Client: returns queued responses in order."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def post(self, url, headers, json):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.answers.pop(0)


def test_gemini_read_is_cached_and_skips_thoughts(tmp_path, cfg, page):
    client = FakeHttp(gemini(reading(id_number=[cand("000000018")])))
    reader = GeminiReader(cfg, VlmCache(tmp_path), client=client, model="gemini-x")
    first = reader.read_page(page, "sha-1", "prompt")
    again = reader.read_page(page, "sha-1", "prompt")
    assert not first.cached and again.cached and len(client.calls) == 1
    call = client.calls[0]
    assert "models/gemini-x:generateContent" in call["url"]
    assert call["headers"] == {"x-goog-api-key": ""}
    gen = call["json"]["generationConfig"]
    assert gen["responseMimeType"] == "application/json"
    assert gen["responseJsonSchema"]["title"] == "PageReading"
    image = call["json"]["contents"][0]["parts"][0]["inline_data"]
    assert image["mime_type"] == "image/jpeg"
    assert first.usage == {"input_tokens": 1200, "output_tokens": 250}
    assert to_candidates(first, "d/p01")[0].model == "gemini-x"


def test_gemini_invalid_json_is_retried_once_then_raises(tmp_path, cfg, page):
    client = FakeHttp(gemini(text="{not json"), gemini(reading(), finish="MAX_TOKENS"))
    reader = GeminiReader(cfg, VlmCache(tmp_path), client=client, model="gemini-x")
    with pytest.raises(VlmInvalid):
        reader.read_page(page, "s", "p")
    assert len(client.calls) == 2 and not list(tmp_path.rglob("*.json"))


def test_gemini_safety_block_is_a_refusal(tmp_path, cfg, page):
    client = FakeHttp(gemini(reading(), finish="SAFETY"))
    reader = GeminiReader(cfg, VlmCache(tmp_path), client=client, model="gemini-x")
    with pytest.raises(VlmInvalid, match="refused"):
        reader.read_page(page, "s", "p")
    assert len(client.calls) == 1


def test_gemini_bad_request_is_not_retried(tmp_path, cfg, page):
    err = SimpleNamespace(
        status_code=400,
        text="{}",
        json=lambda: {"error": {"message": "model not found"}},
    )
    client = FakeHttp(err)
    reader = GeminiReader(cfg, VlmCache(tmp_path), client=client, model="gemini-x")
    with pytest.raises(VlmInvalid, match="model not found"):
        reader.read_page(page, "s", "p")
    assert len(client.calls) == 1


def test_reader_is_picked_by_model_name(tmp_path, cfg):
    cache = VlmCache(tmp_path)
    g = make_reader("live", cfg, cache, "a", model="gemini-x", gemini_api_key="g")
    c = make_reader("live", cfg, cache, "a", model="claude-x", gemini_api_key="g")
    assert isinstance(g, GeminiReader) and g.api_key == "g"
    assert isinstance(c, ClaudeReader)
    assert isinstance(make_reader("replay", cfg, cache, model="gemini-x"), ReplayReader)


BLOCK_PAGE = (
    '<!DOCTYPE html><html><body><iframe src="//netfree.link/block/#%7B%22url'
    '%22:%22https:%2F%2Fapi.anthropic.com%2Fv1%2Fmessages%22%7D"></iframe>'
)


def test_gemini_filter_block_page_is_named_not_parsed(tmp_path, cfg, page):
    html = SimpleNamespace(status_code=418, text=BLOCK_PAGE, json=lambda: 1 / 0)
    reader = GeminiReader(cfg, VlmCache(tmp_path), client=FakeHttp(html), model="g")
    with pytest.raises(VlmInvalid, match="filter blocked"):
        reader.read_page(page, "s", "p")


def test_page_error_explains_a_filter_block_in_hebrew():
    from vera.batch import page_error

    msg = page_error(RuntimeError(BLOCK_PAGE))
    assert "NetFree" in msg and "api.anthropic.com" in msg and "<html>" not in msg
    assert page_error(ValueError("boom")) == "ValueError: boom"
