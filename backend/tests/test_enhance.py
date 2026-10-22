"""Skewed and dark scans (vera.pipeline.enhance, D-021). Synthetic pages."""

import numpy as np
from PIL import Image, ImageDraw

from vera.pipeline.enhance import estimate_skew, fix_page


def ruled_page(tilt: float = 0.0, paper: int = 255) -> Image.Image:
    """A form-like page: long ruled lines, tilted `tilt` degrees (CCW)."""
    img = Image.new("RGB", (1200, 1700), (paper,) * 3)
    d = ImageDraw.Draw(img)
    for y in range(200, 1600, 120):
        d.line((100, y, 1100, y), fill=(0, 0, 0), width=3)
    if tilt:
        img = img.rotate(tilt, expand=True, fillcolor=(paper,) * 3)
    return img


def test_level_page_comes_back_untouched():
    img = ruled_page()
    out, fix = fix_page(img)
    assert out is img  # same pixels: cached reads still match
    assert fix.skew == 0 and not fix.deskewed and not fix.brightened


def test_tilt_is_measured_and_levelled_only_when_asked():
    img = ruled_page(tilt=3.0)
    assert abs(estimate_skew(img) + 3.0) <= 0.3
    out, fix = fix_page(img)  # deskew off by default
    assert out is img and abs(fix.skew) >= 2.7
    out, fix = fix_page(img, deskew_min=1.0)
    assert fix.deskewed and abs(estimate_skew(out)) <= 0.3


def test_page_without_ruled_lines_has_unknown_skew():
    assert estimate_skew(Image.new("RGB", (800, 1000), "white")) == 0.0


def test_dark_scan_is_brightened():
    img = ruled_page(paper=90)
    out, fix = fix_page(img)
    assert fix.brightened and fix.mean < 150
    assert np.asarray(out.convert("L")).mean() > 180
