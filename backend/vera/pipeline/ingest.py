"""Stage 1, ingest (spec 7): source file -> one RGB image per page.

- PDF pages are rendered at a fixed DPI (default 300).
- TIFF (scanners, fax) may hold several pages: one page per frame.
- JPG/PNG/HEIC/WEBP/BMP photos: EXIF orientation applied, metadata dropped.
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

SourceKind = Literal["pdf", "jpeg", "png", "heic", "tiff", "webp", "bmp"]


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
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:2] == b"BM":
        return "bmp"
    raise UnsupportedFileError(
        "סוג קובץ שלא נתמך: אפשר PDF, TIFF, JPEG, PNG, HEIC, WEBP או BMP"
    )


def fingerprint(img: Image.Image) -> str:
    """SHA-256 over mode, size and raw pixels."""
    h = hashlib.sha256(f"{img.mode}:{img.width}x{img.height}:".encode())
    h.update(img.tobytes())
    return h.hexdigest()


def pdf_page_count(path: Path) -> int:
    with pymupdf.open(path) as doc:
        return doc.page_count


def page_count(path: Path) -> int:
    """Pages in a file, checked before any work is queued: a damaged or
    password-protected file, an empty one or one over the limit is turned
    away with a reason a person can act on."""
    kind = sniff_kind(Path(path).read_bytes()[:16])
    try:
        if kind == "pdf":
            with pymupdf.open(path) as doc:
                if doc.needs_pass:
                    raise UnsupportedFileError(
                        "ה-PDF מוגן בסיסמה: צריך לשמור אותו בלי סיסמה"
                    )
                count = doc.page_count
        else:
            with Image.open(path) as img:
                count = getattr(img, "n_frames", 1) if kind == "tiff" else 1
                img.load()  # a truncated photo fails here, not in the queue
    except UnsupportedFileError:
        raise
    except Exception as e:
        raise UnsupportedFileError("הקובץ פגום ולא נפתח") from e
    if count < 1:
        raise UnsupportedFileError("אין בקובץ עמודים")
    if count > MAX_PAGES_PER_FILE:
        raise UnsupportedFileError(
            f"{count} עמודים, יותר מ-{MAX_PAGES_PER_FILE} בקובץ אחד"
        )
    return count


def load_page(path: Path, index: int, dpi: int = DEFAULT_DPI) -> Image.Image:
    """Page `index` (0-based) of any supported file, as RGB."""
    kind = sniff_kind(Path(path).read_bytes()[:16])
    if kind == "pdf":
        return render_pdf_page(path, index, dpi)
    if kind == "tiff":
        with Image.open(path) as img:
            img.seek(index)
            return ImageOps.exif_transpose(img.convert("RGB")).copy()
    return load_photo(Path(path).read_bytes())


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

    count = page_count(path)
    for i in range(count):
        yield RawPage(
            path.stem, i + 1, load_page(path, i, dpi), dpi if kind == "pdf" else None
        )
