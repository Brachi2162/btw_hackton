"""Stage 7, vision-model reader (spec 8): one full-page read per page.

- `VisionReader` is the interface; `GeminiReader` and `ClaudeReader` call the
  APIs (picked by model name, D-006), and `ReplayReader` answers from the
  cache only, with no network (demo mode).
- Output is structured: the response schema is `PageReading` below, and every
  answer is validated by Pydantic. An invalid answer is retried once, then
  raises `VlmInvalid` (reason code `vlm_invalid`).
- Cache key = page pixel fingerprint + preprocessing + rendered prompt + model
  + crop spec, so a rerun costs nothing and replay works offline.
- Privacy (spec 8.4): the page is downscaled and re-encoded without metadata.

The model's own positions are not trusted (spec 7.1): candidates carry a
coarse region and `bbox_approx`; anchoring to OCR/barcode boxes comes later.
"""

import base64
import hashlib
import io
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

import yaml
from PIL import Image
from pydantic import BaseModel, ValidationError

from vera.models import Candidate, PageKind

Region = Literal[
    "top-left",
    "top-center",
    "top-right",
    "middle-left",
    "middle-center",
    "middle-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
]


class VlmCandidate(BaseModel):
    value_raw: str
    legible: bool
    handwritten: bool
    label_nearby: str
    evidence_text: str
    region: Region


class VlmFields(BaseModel):
    first_name: list[VlmCandidate]
    last_name: list[VlmCandidate]
    id_number: list[VlmCandidate]
    central_numbers: list[VlmCandidate]
    receipt_date: list[VlmCandidate]
    sender: list[VlmCandidate]
    commitment_numbers: list[VlmCandidate]
    # Helper fields (spec 5): not exported, used to check the receipt date.
    form_date: list[VlmCandidate]
    sample_date: list[VlmCandidate]


class PageReading(BaseModel):
    """The response schema of a full-page read (spec 8.2.1)."""

    page_kind: PageKind
    upright: bool  # the model's own orientation report, an extra signal only
    sender_guess: str  # an id/name from the sender list, or "unknown"
    page_marker: str  # "Page 1 of 2" etc. as written, else ""
    fields: VlmFields


class CropReadingV1(BaseModel):
    """The response schema of crop_patient.v1 (spec 8.2.2, D-005)."""

    first_name: str
    last_name: str
    id_number: str
    name_legible: bool
    id_legible: bool


class CropReading(BaseModel):
    """The response schema of crop_patient.v2 (D-012). The letters come
    before the names: the model transcribes what it sees first, then joins
    it, instead of jumping to a familiar name. The box (0-1000 of the crop)
    is where it read them, for a second, tighter crop."""

    name_line: str
    first_name_letters: list[str]
    first_name: str
    last_name_letters: list[str]
    last_name: str
    id_number: str
    first_name_legible: bool
    last_name_legible: bool
    id_legible: bool
    box_left: int
    box_top: int
    box_right: int
    box_bottom: int


class CropReadingV3(BaseModel):
    """The response schema of crop_patient.v3 (D-014): v1's plain reading,
    with a confidence per field and a second reading of each name when one
    is still reasonable, offered to the person instead of chosen silently."""

    first_name: str
    first_name_alt: str
    last_name: str
    last_name_alt: str
    id_number: str
    first_name_legible: bool
    last_name_legible: bool
    id_legible: bool


def crop_schema(prompt_version: str) -> type[BaseModel]:
    """Each crop prompt has its own schema; v1 answers stay in the cache."""
    if prompt_version.endswith(".v1"):
        return CropReadingV1
    if prompt_version.endswith(".v3"):
        return CropReadingV3
    return CropReading


class VlmInvalid(RuntimeError):
    """The model did not return a valid PageReading (twice), or refused."""


class CacheMiss(LookupError):
    """Replay mode and this page/prompt/model was never read live."""


@dataclass
class VlmConfig:
    model: str
    escalation_model: str
    effort: str
    max_tokens: int
    prompt: str
    max_side: int
    jpeg_quality: int
    timeout: float
    max_retries: int
    concurrency: int
    commitment_aliases: list[str] = field(default_factory=list)
    crop_model: str = ""
    crop_prompt: str = ""
    crop_max_side: int = 1568
    # Re-read a tight crop around the box the first crop read reported.
    crop_zoom: bool = True
    # A crop reader from another provider, its own source (vlm_crop_check).
    crop_check_model: str = ""

    @classmethod
    def load(cls, config_dir: Path) -> "VlmConfig":
        data = yaml.safe_load((config_dir / "vlm.yaml").read_text(encoding="utf-8"))
        return cls(**data)


def sender_names(config_dir: Path) -> list[str]:
    senders = yaml.safe_load((config_dir / "senders.yaml").read_text(encoding="utf-8"))
    return [s["name"] for s in senders]


def render_prompt(
    prompts_dir: Path,
    name: str,
    commitment_aliases: list[str],
    senders: list[str],
    profile_hints: str = "",
) -> str:
    template = (prompts_dir / f"{name}.md").read_text(encoding="utf-8")
    return template.format(
        commitment_aliases=", ".join(f'"{a}"' for a in commitment_aliases),
        sender_list="; ".join(senders),
        profile_hints=profile_hints or "none",
    )


def prepare_image(img: Image.Image, max_side: int, quality: int) -> bytes:
    """Downscale and encode as JPEG. A fresh RGB image carries no EXIF/GPS."""
    img = img.convert("RGB")
    scale = max_side / max(img.size)
    if scale < 1:
        size = (round(img.width * scale), round(img.height * scale))
        img = img.resize(size, Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


MAX_UPSCALE = 3.0


def crop_image(img: Image.Image, crop, max_side: int) -> Image.Image:
    """A crop is a zoom: a small one (one line of handwriting) is enlarged
    toward max_side, so the model sees the strokes, not a few pixels."""
    if not crop:
        return img
    img = img.crop(crop)
    scale = min(MAX_UPSCALE, max_side / max(img.size))
    if scale > 1:
        size = (round(img.width * scale), round(img.height * scale))
        img = img.resize(size, Image.Resampling.LANCZOS)
    return img


def cache_key(
    page_sha256: str,
    prompt: str,
    model: str,
    cfg: VlmConfig,
    crop_spec: str = "",
    prompt_version: str | None = None,
    max_side: int | None = None,
) -> str:
    h = hashlib.sha256()
    for part in (
        page_sha256,
        f"{max_side or cfg.max_side}:{cfg.jpeg_quality}",
        prompt_version or cfg.prompt,
        hashlib.sha256(prompt.encode()).hexdigest(),
        model,
        cfg.effort,
        crop_spec,
    ):
        h.update(part.encode())
        h.update(b"\x00")
    return h.hexdigest()


@dataclass
class VlmResult:
    reading: BaseModel  # PageReading, or CropReading for crop reads
    model: str
    prompt_version: str
    cache_key: str
    cached: bool
    usage: dict = field(default_factory=dict)


class VlmCache:
    """One JSON file per answer: <dir>/<key[:2]>/<key>.json."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)

    def _path(self, key: str) -> Path:
        return self.dir / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict | None:
        p = self._path(key)
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    def put(self, key: str, entry: dict) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(entry, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        tmp.replace(p)  # atomic: a crash never leaves a half-written answer


class VisionReader(Protocol):
    def read_page(
        self,
        img: Image.Image,
        page_sha256: str,
        prompt: str,
        schema: type[BaseModel] = ...,
        prompt_version: str | None = None,
        crop: tuple[int, int, int, int] | None = None,
        max_side: int | None = None,
    ) -> VlmResult: ...


def _key(reader, page_sha256, prompt, prompt_version, crop, max_side) -> str:
    return cache_key(
        page_sha256,
        prompt,
        reader.model,
        reader.cfg,
        ",".join(map(str, crop)) if crop else "",
        prompt_version,
        max_side,
    )


def _from_cache(entry: dict, key: str, schema=PageReading) -> VlmResult:
    return VlmResult(
        reading=schema.model_validate(entry["reading"]),
        model=entry["model"],
        prompt_version=entry["prompt_version"],
        cache_key=key,
        cached=True,
        usage=entry.get("usage", {}),
    )


class ReplayReader:
    """Cache only. Never touches the network (VLM_MODE=replay)."""

    def __init__(self, cfg: VlmConfig, cache: VlmCache, model: str | None = None):
        self.cfg, self.cache = cfg, cache
        self.model = model or cfg.model

    def read_page(
        self,
        img,
        page_sha256,
        prompt,
        schema=PageReading,
        prompt_version=None,
        crop=None,
        max_side=None,
    ) -> VlmResult:
        key = _key(self, page_sha256, prompt, prompt_version, crop, max_side)
        entry = self.cache.get(key)
        if entry is None:
            raise CacheMiss(f"no cached answer for page {page_sha256[:12]}")
        return _from_cache(entry, key, schema)


class ClaudeReader:
    """Live reads through the Anthropic API; every answer goes to the cache."""

    def __init__(
        self,
        cfg: VlmConfig,
        cache: VlmCache,
        client=None,
        model: str | None = None,
        api_key: str | None = None,
    ):
        import anthropic

        self.cfg, self.cache = cfg, cache
        self.model = model or cfg.model
        self.client = client or anthropic.Anthropic(
            api_key=api_key or None, timeout=cfg.timeout, max_retries=cfg.max_retries
        )
        self._slots = threading.Semaphore(cfg.concurrency)

    def _call(self, image_b64: str, prompt: str, schema=PageReading):
        with self._slots:
            return self.client.messages.parse(
                model=self.model,
                max_tokens=self.cfg.max_tokens,
                output_config={"effort": self.cfg.effort},
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": image_b64,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
                output_format=schema,
            )

    def read_page(
        self,
        img,
        page_sha256,
        prompt,
        schema=PageReading,
        prompt_version=None,
        crop=None,
        max_side=None,
    ) -> VlmResult:
        key = _key(self, page_sha256, prompt, prompt_version, crop, max_side)
        entry = self.cache.get(key)
        if entry is not None:
            return _from_cache(entry, key, schema)

        side = max_side or self.cfg.max_side
        img = crop_image(img, crop, side)
        data = prepare_image(img, side, self.cfg.jpeg_quality)
        image_b64 = base64.standard_b64encode(data).decode("ascii")
        problem = ""
        for _attempt in range(2):  # one retry on an invalid answer (spec 8.1)
            try:
                response = self._call(image_b64, prompt, schema)
            except ValidationError as e:
                problem = f"schema: {e.error_count()} errors"
                continue
            if response.stop_reason == "refusal":
                raise VlmInvalid("model refused to read the page")
            if response.stop_reason == "max_tokens":
                problem = "truncated (max_tokens)"
                continue
            reading = response.parsed_output
            if reading is None:
                problem = "no parsed output"
                continue
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
            self.cache.put(
                key,
                {
                    "model": self.model,
                    "prompt_version": prompt_version or self.cfg.prompt,
                    "page_sha256": page_sha256,
                    "crop": list(crop) if crop else None,
                    "request_id": getattr(response, "_request_id", None),
                    "usage": usage,
                    "reading": reading.model_dump(mode="json"),
                },
            )
            return VlmResult(
                reading,
                self.model,
                prompt_version or self.cfg.prompt,
                key,
                False,
                usage,
            )
        raise VlmInvalid(problem)


GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
# Gemini finish reasons that mean "won't answer", like Claude's refusal.
_GEMINI_BLOCKED = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}


class GeminiReader:
    """Live reads through the Gemini REST API (D-006); same cache and contract
    as ClaudeReader. JSON output is constrained by the schema and then
    validated by Pydantic, never trusted as is."""

    def __init__(
        self,
        cfg: VlmConfig,
        cache: VlmCache,
        client=None,
        model: str | None = None,
        api_key: str | None = None,
    ):
        import httpx

        self.cfg, self.cache = cfg, cache
        self.model = model or cfg.model
        self.api_key = api_key or ""
        self.client = client or httpx.Client(timeout=cfg.timeout)
        self._slots = threading.Semaphore(cfg.concurrency)

    def _post(self, body: dict) -> dict:
        """One call, retried with backoff on 429/5xx/connection errors."""
        import httpx

        problem = ""
        for attempt in range(self.cfg.max_retries + 1):
            if attempt:
                time.sleep(min(2**attempt, 20))
            try:
                with self._slots:
                    r = self.client.post(
                        GEMINI_URL.format(model=self.model),
                        headers={"x-goog-api-key": self.api_key},
                        json=body,
                    )
            except httpx.TransportError as e:
                problem = type(e).__name__
                continue
            if r.status_code == 429 or r.status_code >= 500:
                problem = f"HTTP {r.status_code}"
                continue
            if r.status_code != 200:
                if filter_blocked(r.text):
                    raise VlmInvalid(f"filter blocked: {r.text[:200]}")
                # The error body names the problem (bad key, model name), never the key.
                try:
                    msg = r.json().get("error", {}).get("message", "")[:200]
                except ValueError:
                    msg = r.text[:200]
                raise VlmInvalid(f"gemini HTTP {r.status_code}: {msg}")
            return r.json()
        raise VlmInvalid(f"gemini unavailable: {problem}")

    def read_page(
        self,
        img,
        page_sha256,
        prompt,
        schema=PageReading,
        prompt_version=None,
        crop=None,
        max_side=None,
    ) -> VlmResult:
        key = _key(self, page_sha256, prompt, prompt_version, crop, max_side)
        entry = self.cache.get(key)
        if entry is not None:
            return _from_cache(entry, key, schema)

        side = max_side or self.cfg.max_side
        img = crop_image(img, crop, side)
        data = prepare_image(img, side, self.cfg.jpeg_quality)
        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": base64.standard_b64encode(data).decode("ascii"),
                            }
                        },
                        {"text": prompt},
                    ],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema.model_json_schema(),
                "maxOutputTokens": self.cfg.max_tokens,
            },
        }
        problem = ""
        for _attempt in range(2):  # one retry on an invalid answer (spec 8.1)
            response = self._post(body)
            if response.get("promptFeedback", {}).get("blockReason"):
                raise VlmInvalid("model refused to read the page")
            candidates = response.get("candidates") or []
            if not candidates:
                problem = "no candidates"
                continue
            finish = candidates[0].get("finishReason", "")
            if finish in _GEMINI_BLOCKED:
                raise VlmInvalid("model refused to read the page")
            if finish == "MAX_TOKENS":
                problem = "truncated (max_tokens)"
                continue
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            try:
                reading = schema.model_validate_json(text)
            except ValidationError as e:
                problem = f"schema: {e.error_count()} errors"
                continue
            meta = response.get("usageMetadata", {})
            usage = {
                "input_tokens": meta.get("promptTokenCount", 0),
                "output_tokens": meta.get("candidatesTokenCount", 0)
                + meta.get("thoughtsTokenCount", 0),
            }
            self.cache.put(
                key,
                {
                    "model": self.model,
                    "prompt_version": prompt_version or self.cfg.prompt,
                    "page_sha256": page_sha256,
                    "crop": list(crop) if crop else None,
                    "request_id": response.get("responseId"),
                    "usage": usage,
                    "reading": reading.model_dump(mode="json"),
                },
            )
            return VlmResult(
                reading,
                self.model,
                prompt_version or self.cfg.prompt,
                key,
                False,
                usage,
            )
        raise VlmInvalid(problem)


def filter_blocked(text: str) -> bool:
    """A network content filter (NetFree) answered with its block page
    instead of the API's answer."""
    return "netfree.link/block" in text


def provider_of(model: str) -> str:
    """Which API serves a model name: "gemini" or "anthropic"."""
    return "gemini" if model.startswith("gemini") else "anthropic"


def make_reader(
    mode: str,
    cfg: VlmConfig,
    cache: VlmCache,
    api_key: str = "",
    model: str | None = None,
    gemini_api_key: str = "",
) -> VisionReader:
    if mode == "replay":
        return ReplayReader(cfg, cache, model=model)
    if provider_of(model or cfg.model) == "gemini":
        return GeminiReader(cfg, cache, api_key=gemini_api_key, model=model)
    return ClaudeReader(cfg, cache, api_key=api_key, model=model)


def to_candidates(
    result: VlmResult, page_id: str, reader: str = "vlm_full"
) -> list[Candidate]:
    """Flatten a reading into decision-stage candidates (spec 9.1)."""
    out: list[Candidate] = []
    for name, cands in result.reading.fields:
        for c in cands:
            if not c.value_raw.strip():
                continue
            out.append(
                Candidate(
                    field=name,
                    value_raw=c.value_raw,
                    reader=reader,
                    model=result.model,
                    page_id=page_id,
                    bbox_approx=True,
                    evidence_text=c.evidence_text or None,
                    legible=c.legible,
                    handwritten=c.handwritten,
                    label_nearby=c.label_nearby or None,
                    signals={"region": c.region},
                )
            )
    return out


# Coarse page regions the full-page read reports -> crop box fractions.
_ROWS = {"top": (0.0, 0.45), "middle": (0.25, 0.75), "bottom": (0.55, 1.0)}
_COLS = {"left": (0.0, 0.6), "center": (0.2, 0.8), "right": (0.4, 1.0)}


def region_box(region: str, width: int, height: int) -> tuple[int, int, int, int]:
    """A generous crop around a 3x3 region name like "top-right"."""
    row, _, col = (region or "top-center").partition("-")
    y0, y1 = _ROWS.get(row, _ROWS["top"])
    x0, x1 = _COLS.get(col, _COLS["center"])
    return (int(x0 * width), int(y0 * height), int(x1 * width), int(y1 * height))


# Tight crop around the box a crop read reported: margins as a share of the
# box, and a floor so one squeezed line still has its neighbours' context.
ZOOM_MARGIN_X, ZOOM_MARGIN_Y = 0.15, 0.6
ZOOM_MIN_SIDE = 160
# A box this big (share of the crop's area) is no zoom: read it as it is.
ZOOM_MAX_AREA = 0.6


def zoom_box(
    reading: BaseModel, crop: tuple[int, int, int, int]
) -> tuple[int, int, int, int] | None:
    """The crop read's own box (0-1000 of the crop) -> page pixels, with
    margins. None when the box is missing (a v1 read has none), broken or
    not much smaller than the crop."""
    x0, y0, x1, y1 = crop
    w, h = x1 - x0, y1 - y0
    sides = ("box_left", "box_top", "box_right", "box_bottom")
    b = [max(0, min(1000, getattr(reading, k, 0))) for k in sides]
    if b[2] <= b[0] or b[3] <= b[1]:
        return None
    if (b[2] - b[0]) * (b[3] - b[1]) > ZOOM_MAX_AREA * 1000 * 1000:
        return None
    bx0, by0 = x0 + b[0] * w / 1000, y0 + b[1] * h / 1000
    bx1, by1 = x0 + b[2] * w / 1000, y0 + b[3] * h / 1000
    mx = max((bx1 - bx0) * ZOOM_MARGIN_X, (ZOOM_MIN_SIDE - (bx1 - bx0)) / 2, 0)
    my = max((by1 - by0) * ZOOM_MARGIN_Y, (ZOOM_MIN_SIDE - (by1 - by0)) / 2, 0)
    return (
        int(max(x0, bx0 - mx)),
        int(max(y0, by0 - my)),
        int(min(x1, bx1 + mx)),
        int(min(y1, by1 + my)),
    )


def _letters_match(letters: list[str], value: str) -> bool:
    """The joined letters are the name the model returned. When they aren't,
    the model contradicted its own transcription: not a clear read."""
    return "".join(letters).replace(" ", "") == value.replace(" ", "")


def crop_to_candidates(
    result: VlmResult,
    page_id: str,
    box: tuple[int, int, int, int],
    reader: str = "vlm_crop",
) -> list[Candidate]:
    """A crop read of the patient area -> name/ID candidates."""
    r = result.reading
    alts = {}
    if isinstance(r, CropReadingV1):
        first_ok = last_ok = r.name_legible
        line = ""
    elif isinstance(r, CropReadingV3):
        first_ok, last_ok, line = r.first_name_legible, r.last_name_legible, ""
        alts = {"first_name": r.first_name_alt, "last_name": r.last_name_alt}
    else:
        first_ok = r.first_name_legible and _letters_match(
            r.first_name_letters, r.first_name
        )
        last_ok = r.last_name_legible and _letters_match(
            r.last_name_letters, r.last_name
        )
        line = r.name_line
    readings = [
        ("first_name", r.first_name, first_ok),
        ("last_name", r.last_name, last_ok),
        ("id_number", r.id_number, r.id_legible),
    ]
    # A second reasonable reading is a guess by definition: it competes as an
    # unreadable candidate, so the person gets both and nothing is chosen.
    readings += [
        (f, alt, False)
        for f, alt in alts.items()
        if alt.strip() and alt.strip() != getattr(r, f).strip()
    ]
    out = []
    for field_name, value, legible in readings:
        if not value.strip():
            continue
        out.append(
            Candidate(
                field=field_name,
                value_raw=value,
                reader=reader,
                model=result.model,
                page_id=page_id,
                bbox=box,
                bbox_approx=True,
                evidence_text=(line or f"crop {','.join(map(str, box))}")[:200],
                legible=legible,
                handwritten=True,
                signals={"crop": True},
            )
        )
    return out
