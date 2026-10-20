"""Stages 1+3 together: source files -> upright page images + manifest.

Each page is independent, so pages are processed in a process pool
(CPU-bound: rendering + OCR scoring).
"""

import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from vera.pipeline.ingest import (
    DEFAULT_DPI,
    fingerprint,
    load_photo,
    pdf_page_count,
    render_pdf_page,
    sniff_kind,
)
from vera.pipeline.orient import detect_orientation, rotate_cw


@dataclass
class PageRecord:
    doc: str
    page: int  # 1-based
    source_file: str
    source_sha256: str  # whole source file
    raw_sha256: str  # page pixels as rendered
    sha256: str  # page pixels after rotation: the key for caches downstream
    rotation: int  # clockwise degrees applied
    orientation_confidence: float
    orientation_scores: dict[int, float]
    osd_angle: int | None
    osd_confidence: float | None
    width: int
    height: int
    dpi: int | None
    image: str  # path relative to the output dir


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _process_page(
    path: Path, index: int, file_hash: str, out_dir: Path, dpi: int, orient: bool
) -> PageRecord:
    is_pdf = sniff_kind(path.read_bytes()[:16]) == "pdf"
    raw = render_pdf_page(path, index, dpi) if is_pdf else load_photo(path.read_bytes())
    o = detect_orientation(raw) if orient else None
    angle = o.angle if o else 0
    img = rotate_cw(raw, angle)

    rel = Path(path.stem) / f"p{index + 1:02d}.png"
    (out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_dir / rel, compress_level=3)

    return PageRecord(
        doc=path.stem,
        page=index + 1,
        source_file=path.name,
        source_sha256=file_hash,
        raw_sha256=fingerprint(raw),
        sha256=fingerprint(img),
        rotation=angle,
        orientation_confidence=o.confidence if o else 0.0,
        orientation_scores=o.scores if o else {},
        osd_angle=o.osd_angle if o else None,
        osd_confidence=o.osd_confidence if o else None,
        width=img.width,
        height=img.height,
        dpi=dpi if is_pdf else None,
        image=rel.as_posix(),
    )


def extract_pages(
    paths: list[Path],
    out_dir: Path,
    dpi: int = DEFAULT_DPI,
    orient: bool = True,
    workers: int | None = None,
) -> list[PageRecord]:
    """Write upright PNGs to out_dir/<doc>/pNN.png and out_dir/manifest.json."""
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for path in paths:
        path = Path(path)
        with path.open("rb") as f:
            kind = sniff_kind(f.read(16))
        count = pdf_page_count(path) if kind == "pdf" else 1
        file_hash = file_sha256(path)
        jobs += [(path, i, file_hash, out_dir, dpi, orient) for i in range(count)]

    with ProcessPoolExecutor(max_workers=workers) as ex:
        records = list(ex.map(_process_page, *zip(*jobs))) if jobs else []

    manifest = {"dpi": dpi, "pages": [asdict(r) for r in records]}
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return records
