"""Not a commitment form (D-026). Synthetic pages only (iron rule 5)."""

import pytesseract
from PIL import Image

from vera.config import get_settings
from vera.pipeline.formcheck import form_words, looks_like_form


def test_form_words_come_from_config_and_the_sender_list():
    words = form_words(get_settings().config_dir)
    assert "התחייבות" in words and "מרכזי" in words
    assert any("מכבי" in w for w in words)  # a sender's name counts too


def test_a_page_with_no_barcode_and_no_form_word_is_not_a_form(tmp_path, monkeypatch):
    page = tmp_path / "p.png"
    Image.new("RGB", (400, 500), "white").save(page)
    words = ["התחייבות", "מרכזי"]

    monkeypatch.setattr(pytesseract, "image_to_string", lambda *a, **k: "Dear customer")
    assert not looks_like_form(page, [], words)
    assert looks_like_form(page, [{"text": "512340"}], words)  # a form barcode
    # noise "barcodes" that fit no field (14 digits) don't make it a form
    assert not looks_like_form(page, [{"text": "40819117376230"}], words)

    monkeypatch.setattr(
        pytesseract, "image_to_string", lambda *a, **k: "טופס התחייבות לבדיקה"
    )
    assert looks_like_form(page, [], words)
