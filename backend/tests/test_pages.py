import io
import json

import pytest
from PIL import Image

from tests.conftest import make_pdf, requires_tesseract
from vera.pipeline.ingest import (
    UnsupportedFileError,
    fingerprint,
    iter_pages,
    load_photo,
    render_pdf_page,
    sniff_kind,
)
from vera.pipeline.orient import detect_orientation, rotate_cw
from vera.pipeline.pages import extract_pages

# --- ingest ---


@pytest.mark.parametrize(
    "head,kind",
    [
        (b"%PDF-1.7\n", "pdf"),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", "jpeg"),
        (b"\x89PNG\r\n\x1a\n\x00\x00", "png"),
        (b"\x00\x00\x00\x18ftypheic\x00\x00", "heic"),
    ],
)
def test_sniff_kind_by_magic_bytes(head, kind):
    assert sniff_kind(head) == kind


def test_sniff_kind_rejects_other_files():
    with pytest.raises(UnsupportedFileError):
        sniff_kind(b"PK\x03\x04 zip pretending to be pdf")


def test_pdf_renders_a4_at_300dpi(sample_pdf):
    img = render_pdf_page(sample_pdf, 0, dpi=300)
    assert img.mode == "RGB"
    assert abs(img.width - 2480) <= 2 and abs(img.height - 3508) <= 2


def test_iter_pages_yields_every_page(tmp_path):
    pages = list(iter_pages(make_pdf(tmp_path / "three.pdf", pages=3), dpi=50))
    assert [(p.doc, p.page_no) for p in pages] == [
        ("three", 1),
        ("three", 2),
        ("three", 3),
    ]


def test_fingerprint_is_stable_and_pixel_sensitive(sample_pdf):
    a = render_pdf_page(sample_pdf, 0, dpi=50)
    assert fingerprint(a) == fingerprint(render_pdf_page(sample_pdf, 0, dpi=50))
    b = a.copy()
    b.putpixel((0, 0), (1, 2, 3))
    assert fingerprint(a) != fingerprint(b)


def test_photo_exif_rotation_applied_and_metadata_dropped():
    img = Image.new("RGB", (40, 20), "white")
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90 CW to display
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    out = load_photo(buf.getvalue())
    assert out.size == (20, 40)
    assert not out.getexif()


# --- orientation ---


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_rotate_cw_roundtrip(angle, sample_pdf):
    img = render_pdf_page(sample_pdf, 0, dpi=30)
    assert fingerprint(rotate_cw(rotate_cw(img, angle), (360 - angle) % 360)) == (
        fingerprint(img)
    )


@requires_tesseract
@pytest.mark.parametrize("turned", [90, 180, 270])
def test_detects_rotated_page(sample_pdf, turned):
    upright = render_pdf_page(sample_pdf, 0, dpi=200)
    o = detect_orientation(rotate_cw(upright, turned))
    assert (turned + o.angle) % 360 == 0
    assert o.confidence > 2


@requires_tesseract
def test_blank_page_is_left_alone(tmp_path):
    blank = render_pdf_page(make_pdf(tmp_path / "b.pdf", pages=1, text=False), 0, 100)
    assert detect_orientation(blank).angle == 0


# --- end to end ---


def test_extract_pages_writes_images_and_manifest(tmp_path, sample_pdf):
    out = tmp_path / "pages"
    records = extract_pages([sample_pdf], out, dpi=50, orient=False, workers=1)
    assert [r.page for r in records] == [1, 2]
    assert (out / "synthetic" / "p01.png").is_file()
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["pages"][1]["image"] == "synthetic/p02.png"
    assert manifest["pages"][0]["sha256"] == records[0].sha256
