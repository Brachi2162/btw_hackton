"""Google Cloud Vision OCR (D-009): a second, independent reader of the page
text, with real word positions, printed and handwritten.

It never picks a field's value on its own. The decision stage uses it only
to confirm a value another reader found, the way a barcode does: the same
normalized value, found in a line of the page. That adds an independent
source (iron rule 3) and an exact box instead of the model's rough region.

- One call per page (DOCUMENT_TEXT_DETECTION, Hebrew hint). Answers are
  cached by page fingerprint, so a rerun or replay costs nothing.
- Privacy (spec 8.4): a downscaled JPEG without metadata, like the VLM gets.
- Lines are kept, not the raw response: text, words, boxes, confidences.
"""

import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image

from vera.pipeline.vlm import CacheMiss, VlmInvalid, filter_blocked, prepare_image

VISION_URL = "https://vision.googleapis.com/v1/images:annotate"
VERSION = "v1"  # bump when the request or the line format changes
# Words spanned when matching a value: "512 340", "בן דוד", "25 / 08 / 2026".
MAX_SPAN = 4
_LINE_END = {"EOL_SURE_SPACE", "LINE_BREAK"}


@dataclass
class OcrHit:
    text: str  # as Vision read it
    bbox: tuple[int, int, int, int]
    confidence: float
    line: str  # the whole line, for the evidence text


class VisionOcr:
    """Live reads through the Vision REST API, or cache only (replay)."""

    def __init__(
        self,
        cache_dir: Path,
        api_key: str = "",
        mode: str = "live",
        client=None,
        max_side: int = 3000,
        jpeg_quality: int = 90,
        timeout: float = 60,
        max_retries: int = 3,
    ):
        self.dir, self.api_key, self.mode = Path(cache_dir), api_key, mode
        self.max_side, self.jpeg_quality = max_side, jpeg_quality
        self.max_retries = max_retries
        if client is None and mode == "live":
            import httpx

            client = httpx.Client(timeout=timeout)
        self.client = client

    def _path(self, page_sha256: str) -> Path:
        key = f"{page_sha256}-{VERSION}-{self.max_side}"
        return self.dir / key[:2] / f"{key}.json"

    def read(self, img: Image.Image, page_sha256: str) -> list[dict]:
        """Lines of the page: [{"text", "words": [{"text", "bbox", "conf"}]}],
        boxes in pixels of `img`."""
        p = self._path(page_sha256)
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
        if self.mode == "replay":
            raise CacheMiss(f"no cached OCR for page {page_sha256[:12]}")

        data = prepare_image(img, self.max_side, self.jpeg_quality)
        # Boxes come back in pixels of the sent image; map them to `img`.
        scale = max(1.0, max(img.size) / self.max_side)
        body = {
            "requests": [
                {
                    "image": {"content": base64.b64encode(data).decode("ascii")},
                    "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                    "imageContext": {"languageHints": ["he", "en"]},
                }
            ]
        }
        response = self._post(body)["responses"][0]
        if "error" in response:
            raise VlmInvalid(f"vision: {response['error'].get('message', '')[:200]}")
        lines = to_lines(response, scale)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
        return lines

    def _post(self, body: dict) -> dict:
        """One call, retried with backoff on 429/5xx/connection errors."""
        import httpx

        problem = ""
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(min(2**attempt, 20))
            try:
                r = self.client.post(
                    VISION_URL, params={"key": self.api_key}, json=body
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
                try:
                    msg = r.json().get("error", {}).get("message", "")[:200]
                except ValueError:
                    msg = r.text[:200]
                # The error names the problem (API off, billing), never the key.
                raise VlmInvalid(f"vision HTTP {r.status_code}: {msg}")
            return r.json()
        raise VlmInvalid(f"vision unavailable: {problem}")


def _box(poly: dict, scale: float) -> list[int]:
    """x1, y1, x2, y2; Vision leaves out coordinates that are 0."""
    xs = [v.get("x", 0) for v in poly.get("vertices", [])] or [0]
    ys = [v.get("y", 0) for v in poly.get("vertices", [])] or [0]
    return [round(min(xs) * scale), round(min(ys) * scale)] + [
        round(max(xs) * scale),
        round(max(ys) * scale),
    ]


def to_lines(response: dict, scale: float = 1.0) -> list[dict]:
    """Vision's blocks/paragraphs/words -> lines, split where a symbol ends a
    line. Words keep their own box and confidence."""
    lines: list[dict] = []
    for page in response.get("fullTextAnnotation", {}).get("pages", []):
        for block in page.get("blocks", []):
            for para in block.get("paragraphs", []):
                words: list[dict] = []
                for w in para.get("words", []):
                    symbols = w.get("symbols", [])
                    words.append(
                        {
                            "text": "".join(s.get("text", "") for s in symbols),
                            "bbox": _box(w.get("boundingBox", {}), scale),
                            "conf": round(w.get("confidence", 0.0), 3),
                        }
                    )
                    brk = (
                        symbols[-1].get("property", {}).get("detectedBreak", {})
                        if symbols
                        else {}
                    )
                    if brk.get("type") in _LINE_END:
                        lines.append(_line(words))
                        words = []
                if words:
                    lines.append(_line(words))
    return lines


def _line(words: list[dict]) -> dict:
    return {"text": " ".join(w["text"] for w in words), "words": words}


def find_value(
    lines: list[dict], value: str, normalize: Callable[[str], str | None]
) -> OcrHit | None:
    """The shortest run of 1..MAX_SPAN neighbouring words in one line whose
    normalized text equals `value` (joined with and without spaces, in
    either order: Hebrew lines may come out right-to-left). Shortest first:
    normalizers drop letters, so "מרכזי 512 340" would match a number too."""
    for n in range(1, MAX_SPAN + 1):
        for line in lines:
            words = line["words"]
            for i in range(len(words) - n + 1):
                span = words[i : i + n]
                texts = [w["text"] for w in span]
                for joined in {
                    " ".join(texts),
                    "".join(texts),
                    " ".join(reversed(texts)),
                }:
                    if normalize(joined) == value:
                        return OcrHit(
                            text=" ".join(texts),
                            bbox=(
                                min(w["bbox"][0] for w in span),
                                min(w["bbox"][1] for w in span),
                                max(w["bbox"][2] for w in span),
                                max(w["bbox"][3] for w in span),
                            ),
                            confidence=min(w["conf"] for w in span),
                            line=line["text"],
                        )
    return None
