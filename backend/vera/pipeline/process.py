"""The whole reading pipeline for one page, as plain functions (spec 7).

API, CLI and eval call the same code:
1. `prepare_page` (CPU, process pool): render, orient, save PNG, barcodes.
2. `read_vlm` (network, threads): full-page read, cached; also escalation.
3. `decide_page` (pure, vera.pipeline.decide): statuses and explanations.
"""

from dataclasses import asdict
from pathlib import Path

from PIL import Image

from vera.pipeline.barcodes import read_barcodes
from vera.pipeline.pages import _process_page, file_sha256
from vera.pipeline.vlm import CacheMiss, VisionReader, VlmInvalid, to_candidates


def page_id_of(doc: str, page_no: int) -> str:
    return f"{doc}/p{page_no:02d}"


def prepare_page(
    src: Path, index: int, out_dir: Path, dpi: int = 300, orient: bool = True
) -> dict:
    """Render + orient + save one page, then read its barcodes.
    Picklable in and out, so it can run in a process pool."""
    record = _process_page(
        Path(src), index, file_sha256(Path(src)), out_dir, dpi, orient
    )
    with Image.open(Path(out_dir) / record.image) as img:
        barcodes = [b.to_dict() for b in read_barcodes(img.convert("RGB"))]
    return {"record": asdict(record), "barcodes": barcodes}


def read_vlm(
    reader: VisionReader,
    image_path: Path,
    page_sha256: str,
    prompt: str,
    page_id: str,
    reader_name: str = "vlm_full",
) -> dict:
    """One page through the vision model, as the dict decide_page expects.
    Model/cache failures come back as {"error": ...}; one bad page must not
    sink the batch. Network errors are left to the caller."""
    try:
        with Image.open(image_path) as img:
            r = reader.read_page(img, page_sha256, prompt)
    except (VlmInvalid, CacheMiss) as e:
        return {"error": type(e).__name__, "detail": str(e)[:200]}
    return {
        "model": r.model,
        "cached": r.cached,
        "usage": r.usage,
        "page_kind": r.reading.page_kind,
        "upright": r.reading.upright,
        "sender_guess": r.reading.sender_guess,
        "page_marker": r.reading.page_marker,
        "candidates": [
            c.model_dump(mode="json")
            for c in to_candidates(r, page_id, reader=reader_name)
        ],
    }


def needs_escalation(submission) -> bool:
    return any(f.status in ("review", "missing") for f in submission.fields.values())


CROP_FIELDS = ("first_name", "last_name", "id_number")


def needs_crop(submission) -> bool:
    """Names/ID not yet confirmed by two sources: worth a crop read."""
    return any(
        submission.fields[f].status not in ("verified", "human") for f in CROP_FIELDS
    )


def crop_region(page: dict) -> str:
    """Where the patient details are, as the full-page reads saw them: the
    stronger model first, then the most common region, then a default."""
    cands = [c for c in page.get("candidates", []) if c["field"] in CROP_FIELDS]
    cands.sort(key=lambda c: c["reader"] != "vlm_escalated")
    regions = [c.get("signals", {}).get("region") for c in cands]
    regions = [r for r in regions if r]
    return regions[0] if regions else "top-center"


def read_crop(
    reader: VisionReader,
    image_path: Path,
    page_sha256: str,
    prompt: str,
    prompt_version: str,
    max_side: int,
    page_id: str,
    region: str,
) -> dict:
    """Patient-area crop through a third model (spec 8.2.2); same dict shape
    as read_vlm, so merge_reads/decide_page take it as they are."""
    from vera.pipeline.vlm import CropReading, crop_to_candidates, region_box

    try:
        with Image.open(image_path) as img:
            box = region_box(region, img.width, img.height)
            r = reader.read_page(
                img,
                page_sha256,
                prompt,
                schema=CropReading,
                prompt_version=prompt_version,
                crop=box,
                max_side=max_side,
            )
    except (VlmInvalid, CacheMiss) as e:
        return {"error": type(e).__name__, "detail": str(e)[:200]}
    return {
        "model": r.model,
        "cached": r.cached,
        "usage": r.usage,
        "region": region,
        "box": list(box),
        "candidates": [
            c.model_dump(mode="json") for c in crop_to_candidates(r, page_id, box)
        ],
    }
