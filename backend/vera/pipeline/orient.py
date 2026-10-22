"""Stage 3, orientation (spec 7): detect 0/90/180/270 and rotate upright.

Primary signal: OCR quality. The page is OCR'd (downscaled); upright text
yields many confident words, sideways or upside-down text yields few.
Tesseract's own OSD only picks which angle to try first (spec 7, step 3):
when that one pass clearly reads as upright, the other three are skipped,
otherwise all four orientations are scored and the best wins.

`angle` is always the clockwise rotation that makes the page upright.
"""

import re
from dataclasses import dataclass, field

import pytesseract
from PIL import Image

ANGLES = (0, 90, 180, 270)
SCORE_MAX_SIDE = 1400  # px; enough for OCR word confidence, ~4x faster than full
OCR_LANG = "heb+eng"
MIN_CONF = 70
# One OCR pass at OSD's angle is enough above this score. On the 48 sample
# pages x 4 turns, a wrong angle never scored above 1,327 and an upright page
# never below 1,381; OSD's guess was right in all 192 cases.
EARLY_SCORE = 2000
# A "word" worth counting: 3+ letters/digits (Hebrew, Latin or digits).
_WORD = re.compile(r"[א-תA-Za-z0-9]{3,}")


@dataclass
class Orientation:
    angle: int  # clockwise degrees to apply
    confidence: float  # best score / second-best score (>= 1.0)
    scores: dict[int, float] = field(default_factory=dict)
    osd_angle: int | None = None
    osd_confidence: float | None = None


def rotate_cw(img: Image.Image, angle: int) -> Image.Image:
    if angle % 360 == 0:
        return img
    # PIL rotates counter-clockwise; transpose is lossless for right angles.
    op = {
        90: Image.Transpose.ROTATE_270,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_90,
    }[angle % 360]
    return img.transpose(op)


def _downscale(img: Image.Image, max_side: int) -> Image.Image:
    scale = max_side / max(img.size)
    if scale >= 1:
        return img
    return img.resize((round(img.width * scale), round(img.height * scale)))


def ocr_score(img: Image.Image) -> float:
    """Sum of confidences of confidently-read real words (higher = upright)."""
    data = pytesseract.image_to_data(
        img, lang=OCR_LANG, config="--psm 11", output_type=pytesseract.Output.DICT
    )
    total = 0.0
    for text, conf in zip(data["text"], data["conf"]):
        conf = float(conf)
        if conf >= MIN_CONF and _WORD.fullmatch(text.strip()):
            total += conf
    return total


def osd(img: Image.Image) -> tuple[int | None, float | None]:
    try:
        out = pytesseract.image_to_osd(img, config="--psm 0")
    except pytesseract.TesseractError:  # too little text
        return None, None
    rot = re.search(r"Rotate: (\d+)", out)
    conf = re.search(r"Orientation confidence: ([\d.]+)", out)
    return (
        int(rot.group(1)) if rot else None,
        float(conf.group(1)) if conf else None,
    )


def detect_orientation(img: Image.Image) -> Orientation:
    """OSD's guess first, checked by one OCR pass: a score above EARLY_SCORE
    is upright text, and the other three passes are skipped. Otherwise (or
    when OSD has no guess) all four angles are scored, as before."""
    small = _downscale(img.convert("L"), SCORE_MAX_SIDE)
    osd_angle, osd_conf = osd(small)
    first = osd_angle if osd_angle in ANGLES else 0
    scores = {first: ocr_score(rotate_cw(small, first))}
    if scores[first] >= EARLY_SCORE:
        return Orientation(
            angle=first,
            # A lower bound: the other angles were not read.
            confidence=round(scores[first] / EARLY_SCORE, 2),
            scores={first: round(scores[first], 1)},
            osd_angle=osd_angle,
            osd_confidence=osd_conf,
        )
    for a in ANGLES:
        if a not in scores:
            scores[a] = ocr_score(rotate_cw(small, a))
    ranked = sorted(scores, key=scores.get, reverse=True)
    best, second = scores[ranked[0]], scores[ranked[1]]
    confidence = best / second if second > 0 else (float("inf") if best else 1.0)
    return Orientation(
        angle=ranked[0] if best > 0 else 0,
        confidence=round(confidence, 2),
        scores={a: round(scores[a], 1) for a in ANGLES},
        osd_angle=osd_angle,
        osd_confidence=osd_conf,
    )
