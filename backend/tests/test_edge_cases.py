"""Edge cases: hard inputs and bad moments, end to end through the API with a
fake vision model. Each one is something that happens in a real lab: a fax
saved as TIFF, a password on a PDF, a model writing "לא קריא", a restart in
the middle of a batch. Synthetic values only (iron rule 5)."""

import asyncio
import io
from datetime import date

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.conftest import make_pdf
from tests.test_api import FakeReader, wait_done
from vera.batch import BatchService, Readers
from vera.config import get_settings
from vera.db import PageRow
from vera.models import Candidate
from vera.normalize import is_placeholder, norm_central, norm_id, norm_name, parse_date
from vera.pipeline.decide import DecisionConfig, decide_field

REF = date(2026, 9, 1)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    monkeypatch.setenv("CPU_WORKERS", "1")
    get_settings.cache_clear()
    from vera.api.main import app

    with TestClient(app) as c:
        svc = app.state.batches
        svc.dpi, svc.orient = 60, False
        svc._readers = Readers(FakeReader("m1"), FakeReader("m2"), "prompt")
        bid = c.post("/api/batches", json={"name": "t", "ref_date": "2026-09-01"})
        yield c, svc, bid.json()["id"]
    get_settings.cache_clear()


def send(c, bid, name, data):
    return c.post(f"/api/batches/{bid}/files", files={"files": (name, data)}).json()


def image_bytes(fmt, mode="RGB", size=(600, 800), **kw):
    buf = io.BytesIO()
    Image.new(mode, size, "white" if mode != "1" else 1).save(buf, fmt, **kw)
    return buf.getvalue()


# --- values a reader writes ---


def test_an_all_zero_id_is_not_an_id():
    assert norm_id("000000000").problems == ["validation_failed"]
    assert norm_central("000000").problems == ["validation_failed"]


@pytest.mark.parametrize(
    "text", ["לא קריא", "לא ידוע", "unknown", "Illegible", "N/A", "?", " (לא ברור) "]
)
def test_placeholders_are_not_values(text):
    assert is_placeholder(text)


def test_real_values_are_not_placeholders():
    assert not any(map(is_placeholder, ["אין־בר", "דנה", "512340", "N"]))


def test_a_placeholder_leaves_the_field_empty_and_flagged():
    cfg = DecisionConfig.load(get_settings().config_dir)
    c = Candidate(
        field="first_name", value_raw="לא קריא", reader="vlm_full", page_id="p"
    )
    fr = decide_field("first_name", [c], [], "p", cfg, REF)
    assert fr.values == [] and fr.status == "review" and fr.reasons == ["illegible"]
    zero = Candidate(
        field="id_number", value_raw="000000000", reader="vlm_full", page_id="p"
    )
    assert decide_field("id_number", [zero], [], "p", cfg, REF).status == "review"


def test_direction_marks_and_year_first_dates():
    assert norm_name("‏דנה‎").value == "דנה"
    assert parse_date("2026-08-01", REF) == date(2026, 8, 1)
    assert parse_date("01/08/2026", REF) == date(2026, 8, 1)
    assert parse_date("2026-02-30", REF) is None


# --- files ---


@pytest.mark.parametrize(
    "name,data,why",
    [
        ("empty.pdf", b"", "סוג קובץ"),
        ("notes.txt", "שלום".encode(), "סוג קובץ"),
        ("broken.pdf", b"%PDF-1.4\n garbage, no objects", "פגום"),
        ("cut.jpg", image_bytes("JPEG")[:200], "פגום"),
    ],
)
def test_bad_files_are_turned_away_with_a_reason(api, name, data, why):
    c, _, bid = api
    r = send(c, bid, name, data)
    assert not r["added"] and why in r["rejected"][0]["error"]
    assert c.get(f"/api/batches/{bid}").json()["documents"] == []


def test_a_password_pdf_is_turned_away(api, tmp_path):
    c, _, bid = api
    doc = pymupdf.open()
    doc.new_page()
    path = tmp_path / "locked.pdf"
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    r = send(c, bid, "locked.pdf", path.read_bytes())
    assert "סיסמה" in r["rejected"][0]["error"]


def test_a_multi_page_fax_tiff_is_read_page_by_page(api):
    c, _, bid = api
    frames = [Image.new("1", (500, 700), 1) for _ in range(3)]  # fax: 1-bit
    buf = io.BytesIO()
    frames[0].save(buf, "TIFF", save_all=True, append_images=frames[1:])
    r = send(c, bid, "fax.tif", buf.getvalue())
    assert r["added"][0]["pages"] == 3
    assert wait_done(c, bid)["counts"]["pages"] == {"done": 3}


@pytest.mark.parametrize(
    "name,data",
    [
        ("alpha.png", image_bytes("PNG", "RGBA")),
        ("palette.png", image_bytes("PNG", "P")),
        ("gray16.png", image_bytes("PNG", "I;16")),
        ("cmyk.jpg", image_bytes("JPEG", "CMYK")),
        ("photo.webp", image_bytes("WEBP")),
        ("scan.bmp", image_bytes("BMP")),
        ("tiny.png", image_bytes("PNG", size=(4, 4))),
    ],
)
def test_odd_images_are_read(api, name, data):
    c, _, bid = api
    assert send(c, bid, name, data)["added"]
    assert wait_done(c, bid)["counts"]["pages"] == {"done": 1}


def test_file_names_are_names_only(api):
    c, _, bid = api
    r = send(c, bid, "../../etc/טופס חדש (1).png", image_bytes("PNG"))
    assert r["added"][0]["filename"] == "טופס חדש (1).png"


# --- bad moments ---


def test_a_restart_mid_batch_leaves_pages_ready_to_retry(api, tmp_path):
    c, svc, bid = api
    send(c, bid, "a.pdf", make_pdf(tmp_path / "a.pdf", pages=2).read_bytes())
    wait_done(c, bid)
    with svc.sv.repo.session() as s:  # as if the server died mid-page
        for p in s.query(PageRow).all():
            p.status = "processing"
            s.add(p)
        s.commit()
    assert BatchService(svc.sv).recover_interrupted() == 0  # the new one did it
    pages = c.get(f"/api/batches/{bid}").json()["counts"]["pages"]
    assert pages == {"failed": 2}
    assert c.post(f"/api/batches/{bid}/retry-failed").json()["requeued"] == 2
    assert wait_done(c, bid)["counts"]["pages"] == {"done": 2}


def test_two_runs_at_once_read_each_page_once(api, tmp_path):
    c, svc, bid = api
    calls = []

    class Counting(FakeReader):
        def read_page(self, img, page_sha256, prompt):
            calls.append((self.model, page_sha256))
            return super().read_page(img, page_sha256, prompt)

    svc._readers = Readers(Counting("m1"), Counting("m2"), "prompt")
    pdf = make_pdf(tmp_path / "b.pdf", pages=3).read_bytes()
    svc.add_file(bid, "b.pdf", pdf, "t")

    async def both():
        await asyncio.gather(svc.process_batch(bid), svc.process_batch(bid))

    asyncio.run(both())
    # 3 pages, each once per model (the fake form has an orange field, so
    # the escalation model reads it too): never twice by the same model
    assert sorted(m for m, _ in calls) == ["m1"] * 3 + ["m2"] * 3


# --- people's input ---


def test_odd_corrections(api, tmp_path):
    c, _, bid = api
    send(c, bid, "a.pdf", make_pdf(tmp_path / "a.pdf", pages=1).read_bytes())
    sid = wait_done(c, bid)["submissions"][0]["id"]
    url = f"/api/submissions/{sid}/fields"
    assert c.patch(f"{url}/nope", json={"values": ["x"]}).status_code in (404, 422)
    assert c.patch(f"{url}/first_name", json={"values": ["א" * 500]}).status_code == 422
    r = c.patch(f"{url}/receipt_date", json={"values": ["2026-08-20"]})
    assert r.status_code == 200
    assert r.json()["fields"]["receipt_date"]["values"] == ["2026-08-20"]
    assert c.patch(f"{url}/first_name", json={"values": ["‏דנה"]}).status_code == 200
    assert c.get("/api/search", params={"q": "%_'\";--"}).status_code == 200


def test_an_exported_batch_is_read_only(api, tmp_path):
    c, _, bid = api
    send(c, bid, "a.pdf", make_pdf(tmp_path / "a.pdf", pages=1).read_bytes())
    sid = wait_done(c, bid)["submissions"][0]["id"]
    assert (
        c.post(f"/api/batches/{bid}/export", params={"force": True}).status_code == 200
    )
    r = c.patch(f"/api/submissions/{sid}/fields/first_name", json={"values": ["דנה"]})
    assert r.status_code == 409
    assert send(c, bid, "x.png", image_bytes("PNG")) is not None
    assert c.post(f"/api/batches/{bid}/retry-failed").status_code == 409
