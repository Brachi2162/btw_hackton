"""Is this page a commitment form at all? (D-026)

Asked only about a page the readers got nothing from (no value in any
field), so it costs nothing on real forms. A page is not a form when it has
no form barcode and none of the words every form prints somewhere: the field
labels (`form_words` in config/confidence.yaml) and the senders' names
(config/senders.yaml). On the 48 sample pages every page has a barcode or
at least one such word; a photo or an unrelated letter has neither.

A form barcode is one whose value fits a form's number field (a central
number, an ID, a commitment number): on a noisy photo the barcode reader
"finds" 14-digit DataBar codes that fit none of them.

Not a form -> the page stays out of the review table, and intake says so
by file and page. Anything with a form barcode or one form word stays in, as
before (iron rule 1: a form we can't read is flagged, never dropped).
"""

import re
from pathlib import Path

import yaml
from PIL import Image

_OCR_SIDE = 1700  # big enough for printed labels, ~2s a page


def _key(text: str) -> str:
    return re.sub(r"[\s\"'״׳.\-]", "", text or "").lower()


def form_words(config_dir: Path) -> list[str]:
    def y(name):
        return yaml.safe_load((config_dir / name).read_text(encoding="utf-8"))

    words = list(y("confidence.yaml").get("form_words", []))
    for s in y("senders.yaml"):
        words += [s["name"], *s.get("aliases", [])]
    return sorted({k for w in words if len(k := _key(w)) >= 3})


def page_words(img: Image.Image, words: list[str]) -> list[str]:
    """The form words OCR finds on the page (Hebrew + English)."""
    import pytesseract

    g = img.convert("L")
    g.thumbnail((_OCR_SIDE, _OCR_SIDE))
    text = _key(pytesseract.image_to_string(g, lang="heb+eng", config="--psm 3"))
    return [w for w in words if w in text]


def _form_barcode(text: str) -> bool:
    from vera.normalize import norm_central, norm_commitment, norm_id

    return any(not n(text).problems for n in (norm_central, norm_id, norm_commitment))


def looks_like_form(image_path: Path, barcodes: list, words: list[str]) -> bool:
    if any(_form_barcode(b.get("text", "")) for b in barcodes):
        return True
    with Image.open(image_path) as img:
        return bool(page_words(img, words))
