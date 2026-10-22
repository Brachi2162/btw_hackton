import shutil

import pymupdf
import pytest

# Synthetic text only: never values from the sample forms (iron rule 5).
LINES = [
    "Laboratory request form for external testing",
    "Patient details and insurance information",
    "Specimen type serum collected yesterday morning",
    "Please return the results to the referring clinic",
    "Requested tests virology panel and serology",
    "Signature of the physician and department stamp",
] * 6


@pytest.fixture(autouse=True)
def no_real_api_keys(monkeypatch):
    """Tests never reach a real model API, even when .env has keys (the
    container's environment does). A test that needs a key sets a fake one."""
    from vera.config import get_settings

    for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_VISION_API_KEY"):
        monkeypatch.setenv(key, "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_pdf(path, pages=2, text=True):
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)  # A4 in points
        if text:
            for i, line in enumerate(LINES):
                page.insert_text((50, 60 + i * 20), line, fontsize=12)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def sample_pdf(tmp_path):
    return make_pdf(tmp_path / "synthetic.pdf")


requires_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract not installed (run in Docker)"
)
