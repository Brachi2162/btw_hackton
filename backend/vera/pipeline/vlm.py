"""Stage 7, vision-model reader (spec 8): one full-page read per page.

- `VisionReader` is the interface; `ClaudeReader` calls the API, and
  `ReplayReader` answers from the cache only, with no network (demo mode).
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


def cache_key(
    page_sha256: str, prompt: str, model: str, cfg: VlmConfig, crop_spec: str = ""
) -> str:
    h = hashlib.sha256()
    for part in (
        page_sha256,
        f"{cfg.max_side}:{cfg.jpeg_quality}",
        cfg.prompt,
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
    reading: PageReading
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
        self, img: Image.Image, page_sha256: str, prompt: str
    ) -> VlmResult: ...


def _from_cache(entry: dict, key: str) -> VlmResult:
    return VlmResult(
        reading=PageReading.model_validate(entry["reading"]),
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

    def read_page(self, img, page_sha256, prompt) -> VlmResult:
        key = cache_key(page_sha256, prompt, self.model, self.cfg)
        entry = self.cache.get(key)
        if entry is None:
            raise CacheMiss(f"no cached answer for page {page_sha256[:12]}")
        return _from_cache(entry, key)


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

    def _call(self, image_b64: str, prompt: str):
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
                output_format=PageReading,
            )

    def read_page(self, img, page_sha256, prompt) -> VlmResult:
        key = cache_key(page_sha256, prompt, self.model, self.cfg)
        entry = self.cache.get(key)
        if entry is not None:
            return _from_cache(entry, key)

        data = prepare_image(img, self.cfg.max_side, self.cfg.jpeg_quality)
        image_b64 = base64.standard_b64encode(data).decode("ascii")
        problem = ""
        for _attempt in range(2):  # one retry on an invalid answer (spec 8.1)
            try:
                response = self._call(image_b64, prompt)
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
                    "prompt_version": self.cfg.prompt,
                    "page_sha256": page_sha256,
                    "request_id": getattr(response, "_request_id", None),
                    "usage": usage,
                    "reading": reading.model_dump(mode="json"),
                },
            )
            return VlmResult(reading, self.model, self.cfg.prompt, key, False, usage)
        raise VlmInvalid(problem)


def make_reader(
    mode: str, cfg: VlmConfig, cache: VlmCache, api_key: str = ""
) -> VisionReader:
    if mode == "replay":
        return ReplayReader(cfg, cache)
    return ClaudeReader(cfg, cache, api_key=api_key)


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
