"""Stage 3b, page clean-up for edge cases (spec 7): a skewed scan and a
dark scan. Runs after the 90-degree orientation fix, before the page is
fingerprinted, so caches follow the image the readers actually get.

A page that needs no fix comes back as the same image (same pixels, same
fingerprint): the clean-up never invalidates cached reads of good pages.

- Skew: the median angle of the long ruled lines (at least half the page
  wide) that forms print. Text-based estimates were fooled by stamps,
  vertical side text and black bars; ruled lines are what a form's tilt
  is. A page without enough of them (a screenshot) reports 0: unknown.
  Always measured; corrected only from `deskew_min` degrees (0 = off).
- Dark: a page whose mean brightness is below `dark_below` gets a contrast
  stretch and a gamma lift toward a paper-white mean.
"""

import math
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps

MAX_SKEW = 6.0  # steeper lines are not ruled lines of a level-ish page
MIN_LINES = 3  # fewer long lines than this: the angle is unknown (0)
MIN_LINE_SHARE = 0.5  # a ruled line spans at least this share of the width
TARGET_MEAN = 215.0  # paper-like grey level after a dark fix
_SIDE = 1000  # the line search runs on a copy this size


@dataclass
class PageFix:
    skew: float  # degrees to rotate counter-clockwise to level the text
    deskewed: bool
    mean: float  # mean grey level before any fix (0-255)
    brightened: bool


def estimate_skew(img: Image.Image) -> float:
    """Degrees (counter-clockwise) that level the page's ruled lines."""
    g = np.asarray(img.convert("L"))
    f = _SIDE / max(g.shape)
    g = cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    lines = cv2.HoughLinesP(
        cv2.Canny(g, 50, 150),
        1,
        np.pi / 720,
        threshold=200,
        minLineLength=int(g.shape[1] * MIN_LINE_SHARE),
        maxLineGap=8,
    )
    angles = [
        a
        for x1, y1, x2, y2 in (lines[:, 0] if lines is not None else [])
        if abs(a := math.degrees(math.atan2(y2 - y1, x2 - x1))) <= MAX_SKEW
    ]
    if len(angles) < MIN_LINES:
        return 0.0
    return round(float(np.median(angles)), 1)


def brighten(img: Image.Image) -> Image.Image:
    """Stretch the contrast, then lift the mid-tones toward TARGET_MEAN."""
    img = ImageOps.autocontrast(img.convert("RGB"), cutoff=1)
    mean = float(np.asarray(img.convert("L")).mean())
    if 0 < mean < TARGET_MEAN:
        gamma = math.log(TARGET_MEAN / 255) / math.log(mean / 255)
        lut = [round(255 * (i / 255) ** gamma) for i in range(256)]
        img = img.point(lut * 3)
    return img


def fix_page(
    img: Image.Image, deskew_min: float = 0.0, dark_below: float = 150.0
) -> tuple[Image.Image, PageFix]:
    mean = float(np.asarray(img.convert("L").reduce(4)).mean())
    skew = estimate_skew(img)
    deskewed = bool(deskew_min) and abs(skew) >= deskew_min
    brightened = mean < dark_below
    if brightened:
        img = brighten(img)
    if deskewed:
        img = img.convert("RGB").rotate(
            skew,
            resample=Image.Resampling.BICUBIC,
            expand=True,
            fillcolor=(255, 255, 255),
        )
    return img, PageFix(round(skew, 1), deskewed, round(mean, 1), brightened)
