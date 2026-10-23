import numpy as np
import pytest
from PIL import Image

zxingcpp = pytest.importorskip("zxingcpp")

from vera.pipeline.barcodes import Barcode, _same, read_barcodes  # noqa: E402

# Synthetic values only, never from the sample forms (iron rule 5).
PAGE = (2480, 3508)  # A4 at 300dpi


def barcode_img(text: str, fmt=zxingcpp.BarcodeFormat.Code128, width=600, height=150):
    arr = np.asarray(zxingcpp.write_barcode(fmt, text, width=width, height=height))
    return Image.fromarray(arr).convert("RGB")


def page_with(*items: tuple[Image.Image, tuple[int, int]]) -> Image.Image:
    page = Image.new("RGB", PAGE, "white")
    for img, at in items:
        page.paste(img, at)
    return page


def contains(bbox, at, size, slack=30):
    x1, y1, x2, y2 = bbox
    return (
        x1 >= at[0] - slack
        and y1 >= at[1] - slack
        and x2 <= at[0] + size[0] + slack
        and y2 <= at[1] + size[1] + slack
    )


def test_reads_text_format_and_position():
    bc = barcode_img("482910")
    page = page_with((bc, (300, 900)))
    found = read_barcodes(page)
    assert [(b.text, b.format) for b in found] == [("482910", "Code128")]
    assert contains(found[0].bbox, (300, 900), bc.size)


def test_vertical_barcode_is_read():
    bc = barcode_img("A77001", fmt=zxingcpp.BarcodeFormat.Code39).rotate(
        90, expand=True
    )
    page = page_with((bc, (1800, 400)))
    found = read_barcodes(page)
    assert [b.text for b in found] == ["A77001"]
    assert contains(found[0].bbox, (1800, 400), bc.size)


def test_same_value_on_two_stickers_is_kept_twice():
    bc = barcode_img("130507")
    page = page_with((bc, (200, 500)), (bc, (200, 2500)))
    found = read_barcodes(page)
    assert [b.text for b in found] == ["130507", "130507"]
    assert found[0].bbox[1] < found[1].bbox[1]  # sorted top to bottom


def test_several_barcodes_sorted_top_to_bottom():
    page = page_with(
        (barcode_img("00012345678"), (1200, 2000)),
        (barcode_img("615243"), (300, 300)),
    )
    assert [b.text for b in read_barcodes(page)] == ["615243", "00012345678"]


def test_blank_page_has_no_barcodes():
    assert read_barcodes(Image.new("RGB", PAGE, "white")) == []


def test_crop_pass_does_not_duplicate_page_hits():
    page = page_with((barcode_img("907711"), (500, 500)))
    found = read_barcodes(page, crops=True)
    assert len(found) == 1 and found[0].via == "page"


def test_same_requires_overlap():
    a = Barcode("1", "Code128", (0, 0, 100, 50), 0, "page")
    b = Barcode("1", "Code128", (90, 10, 190, 60), 0, "crop")
    c = Barcode("1", "Code128", (500, 0, 600, 50), 0, "crop")
    assert _same(a, b) and not _same(a, c)


def test_labels_are_read_only_around_a_possible_central_number(monkeypatch):
    import pytesseract

    from vera.pipeline.barcodes import label_barcodes

    seen = []

    def ocr(img, **kw):
        seen.append(img.size)
        return "מרכזי"

    monkeypatch.setattr(pytesseract, "image_to_string", ocr)
    central = Barcode("512340", "Code128", (100, 100, 300, 150), 0, "page")
    other = Barcode("ABC-77", "Code128", (100, 400, 300, 450), 0, "page")
    label_barcodes(Image.new("RGB", (800, 800), "white"), [central, other], ["מרכזי"])
    assert central.label == "מרכזי" and other.label is None
    assert len(seen) == 1  # no OCR around a barcode that can't be a central
