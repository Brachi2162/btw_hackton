"""Stage 7, barcode reader (spec 7, 7.1): the most certain source.

Two passes over the upright page:
1. zxing-cpp on the full page.
2. Regions that look like bars (strong gradient in one direction) are cropped
   with a margin, enlarged 2x and read again: small or faxed barcodes are
   often missed at page scale.

Barcodes are deduplicated by (format, text, position). This stage only says
what is encoded where; which field a barcode belongs to (central number,
commitment, ID...) is decided later from the label next to it (spec 3.6).
That label is read here (D-016): printed text right around each barcode, in
three rotations, since stickers often print it sideways ("מרכזי" turned 90°
reads to a model like "CLCI").
"""

import re
from dataclasses import asdict, dataclass
from functools import lru_cache

import cv2
import numpy as np
import zxingcpp
from PIL import Image

from vera.models import BBox

CROP_SCALE = 2
CROP_MARGIN = 40  # px around a bar region, so quiet zones survive the crop
REGION_MAX_SIDE = 1200  # px; region search runs on a downscaled page
MIN_REGION_AREA = 0.0005  # fractions of the page area
MAX_REGION_AREA = 0.06  # a sticker, not a table or a photo
MAX_REGIONS = 40
MAX_REGION_ASPECT = 6


@dataclass
class Barcode:
    text: str
    format: str  # e.g. "Code128", "Code39", "QRCode"
    bbox: BBox  # page pixels, upright image
    orientation: int  # degrees, as zxing reports it
    via: str  # "page" or "crop"
    label: str | None = None  # a field label printed right next to it (D-016)

    def to_dict(self) -> dict:
        return asdict(self)


def _bbox(position, dx: int = 0, dy: int = 0, scale: float = 1.0) -> BBox:
    pts = [
        position.top_left,
        position.top_right,
        position.bottom_right,
        position.bottom_left,
    ]
    xs = [int(p.x / scale) + dx for p in pts]
    ys = [int(p.y / scale) + dy for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _decode(img: Image.Image | np.ndarray, via: str, dx=0, dy=0, scale=1.0):
    out = []
    for r in zxingcpp.read_barcodes(img):
        if not r.valid or not r.text:
            continue
        out.append(
            Barcode(
                text=r.text,
                format=r.format.name,
                bbox=_bbox(r.position, dx, dy, scale),
                orientation=int(r.orientation),
                via=via,
            )
        )
    return out


def find_bar_regions(gray: np.ndarray) -> list[BBox]:
    """Boxes (page pixels) of regions with dense parallel bars, any of the
    two axis directions. Cheap and over-inclusive: zxing decides."""
    h, w = gray.shape
    scale = min(1.0, REGION_MAX_SIDE / max(h, w))
    small = cv2.resize(gray, (int(w * scale), int(h * scale)), cv2.INTER_AREA)
    gx = cv2.convertScaleAbs(cv2.Sobel(small, cv2.CV_32F, 1, 0, ksize=3))
    gy = cv2.convertScaleAbs(cv2.Sobel(small, cv2.CV_32F, 0, 1, ksize=3))
    min_area = MIN_REGION_AREA * small.shape[0] * small.shape[1]
    max_area = MAX_REGION_AREA * small.shape[0] * small.shape[1]

    boxes: list[BBox] = []
    # Vertical bars: gx >> gy, merge along x. Horizontal bars: the opposite.
    for grad, kernel in (
        (cv2.subtract(gx, gy), (21, 5)),
        (cv2.subtract(gy, gx), (5, 21)),
    ):
        blur = cv2.blur(grad, (7, 7))
        _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        closed = cv2.morphologyEx(
            th, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, kernel)
        )
        closed = cv2.dilate(cv2.erode(closed, None, iterations=2), None, iterations=2)
        contours, _ = cv2.findContours(
            closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        for c in contours:
            x, y, cw, ch = cv2.boundingRect(c)
            # Text lines are long and thin; barcode blocks are stubbier.
            if not min_area <= cw * ch <= max_area or max(
                cw, ch
            ) > MAX_REGION_ASPECT * min(cw, ch):
                continue
            boxes.append(
                (
                    int(x / scale),
                    int(y / scale),
                    int((x + cw) / scale),
                    int((y + ch) / scale),
                )
            )
    boxes.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    return boxes[:MAX_REGIONS]


def _same(a: Barcode, b: Barcode) -> bool:
    """Same symbol: same content and centers closer than the symbol's length.
    zxing reports a linear barcode as the scan line it decoded (a few px
    thick), so two reads of one symbol rarely overlap; equal text far away
    is a second sticker and is kept."""
    if a.text != b.text or a.format != b.format:
        return False
    length = max(a.bbox[2] - a.bbox[0], a.bbox[3] - a.bbox[1], 1)
    ax, ay = (a.bbox[0] + a.bbox[2]) / 2, (a.bbox[1] + a.bbox[3]) / 2
    bx, by = (b.bbox[0] + b.bbox[2]) / 2, (b.bbox[1] + b.bbox[3]) / 2
    return abs(ax - bx) < length and abs(ay - by) < length


def _union(a: BBox, b: BBox) -> BBox:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def read_barcodes(img: Image.Image, crops: bool = True) -> list[Barcode]:
    """All barcodes on an upright page, page pass first, then crop pass."""
    found = _decode(img, "page")
    if crops:
        gray = np.asarray(img.convert("L"))
        h, w = gray.shape
        for x1, y1, x2, y2 in find_bar_regions(gray):
            x1, y1 = max(0, x1 - CROP_MARGIN), max(0, y1 - CROP_MARGIN)
            x2, y2 = min(w, x2 + CROP_MARGIN), min(h, y2 + CROP_MARGIN)
            crop = gray[y1:y2, x1:x2]
            big = cv2.resize(
                crop, None, fx=CROP_SCALE, fy=CROP_SCALE, interpolation=cv2.INTER_CUBIC
            )
            for b in _decode(big, "crop", x1, y1, CROP_SCALE):
                twin = next((f for f in found if _same(b, f)), None)
                if twin:  # another scan line of a known symbol: widen its box
                    twin.bbox = _union(twin.bbox, b.bbox)
                else:
                    found.append(b)
    found.sort(key=lambda b: (b.bbox[1], b.bbox[0]))
    return found


LABEL_PAD = 0.6  # of the barcode's longer side: right around it, not the page


@lru_cache
def label_words() -> tuple[str, ...]:
    """Labels that name a barcode's field, from config (the central number's
    anchors): rules live in config, not in code."""
    import yaml

    from vera.config import get_settings

    path = get_settings().config_dir / "confidence.yaml"
    conf = yaml.safe_load(path.read_text(encoding="utf-8"))
    return tuple(conf.get("anchors", {}).get("central_numbers", []))


def label_barcodes(img: Image.Image, barcodes: list[Barcode], words=None) -> None:
    """Set `label` on each barcode with one of `words` printed right around it,
    as a whole word ("המרכזית" in a stamp is not "מרכזי")."""
    import pytesseract

    words = label_words() if words is None else words
    if not words:
        return
    found = re.compile("|".join(rf"(?<![א-ת]){re.escape(w)}(?![א-ת])" for w in words))
    gray = img.convert("L")
    for b in barcodes:
        x1, y1, x2, y2 = b.bbox
        pad = int(max(x2 - x1, y2 - y1) * LABEL_PAD)
        area = gray.crop((max(0, x1 - pad), max(0, y1 - pad), x2 + pad, y2 + pad))
        for angle in (0, 90, 270):
            try:
                text = pytesseract.image_to_string(
                    area.rotate(angle, expand=True), lang="heb", config="--psm 11"
                )
            except pytesseract.TesseractError:
                break
            m = found.search(text)
            if m:
                b.label = m.group(0)
                break
