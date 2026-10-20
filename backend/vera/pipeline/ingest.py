"""Stage 1, ingest (spec 7): source file -> one RGB image per page.

- PDF pages are rendered at a fixed DPI (default 300).
- JPG/PNG/HEIC photos: EXIF orientation applied, metadata dropped.
- File type is decided by magic bytes, never by extension (spec 16).
- Every page gets a SHA-256 fingerprint of its pixels, independent of
  any image encoder, so it can key caches.
"""

import hashlib
import io
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pymupdf
from PIL import Image, ImageOps

try:  # HEIC from iPhones; optional at import time so tests don't need it
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover
    pass

DEFAULT_DPI = 300
MAX_PAGES_PER_FILE = 200  # spec 16: bound work per upload

SourceKind = Literal["pdf", "jpeg", "png", "heic"]


class UnsupportedFileError(ValueError):
    pass


@dataclass
class RawPage:
    doc: str  # source file stem, e.g. "part1"
    page_no: int  # 1-based
    image: Image.Image  # RGB, as rendered/loaded (not yet rotated)
    dpi: int | None  # render DPI for PDFs; None for photos


def sniff_kind(head: bytes) -> SourceKind:
    """Detect the file type from its first bytes."""
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1"):
        return "heic"
    raise UnsupportedFileError("unsupported file type (expected PDF/JPEG/PNG/HEIC)")


def fingerprint(img: Image.Image) -> str:
    """SHA-256 over mode, size and raw pixels."""
    h = hashlib.sha256(f"{img.mode}:{img.width}x{img.height}:".encode())
    h.update(img.tobytes())
    return h.hexdigest()


def pdf_page_count(path: Path) -> int:
    with pymupdf.open(path) as doc:
        return doc.page_count


def render_pdf_page(path: Path, index: int, dpi: int = DEFAULT_DPI) -> Image.Image:
    """Render one PDF page (0-based index) to RGB. Honors the page's /Rotate."""
    with pymupdf.open(path) as doc:
        pix = doc[index].get_pixmap(dpi=dpi, alpha=False, colorspace=pymupdf.csRGB)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def load_photo(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)  # phone photos store rotation in EXIF
    # Copy pixels into a fresh image: drops EXIF/GPS and other metadata.
    return img.convert("RGB").copy()


def iter_pages(path: Path, dpi: int = DEFAULT_DPI) -> Iterator[RawPage]:
    """Yield every page of a PDF or a single photo as a RawPage."""
    path = Path(path)
    with path.open("rb") as f:
        kind = sniff_kind(f.read(16))

    if kind != "pdf":
        yield RawPage(path.stem, 1, load_photo(path.read_bytes()), None)
        return

    count = pdf_page_count(path)
    if count > MAX_PAGES_PER_FILE:
        raise UnsupportedFileError(f"{count} pages > limit {MAX_PAGES_PER_FILE}")
    for i in range(count):
        yield RawPage(path.stem, i + 1, render_pdf_page(path, i, dpi), dpi)
