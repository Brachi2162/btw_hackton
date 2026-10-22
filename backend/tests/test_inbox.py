"""Import from the scanner folder, and failing fast without a reader.
Synthetic files only."""

import os
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.conftest import make_pdf
from tests.test_api import FakeReader, wait_done
from vera.batch import Readers
from vera.config import get_settings
from vera.inbox import InboxError, resolve

HOST = r"C:\Users\lab\Scans"


def old(path):
    """Older than the 'still being written' window."""
    t = time.time() - 3600
    os.utime(path, (t, t))
    return path


@pytest.fixture
def inbox(tmp_path):
    root = tmp_path / "inbox"
    day = root / "2026-10-22"
    (day / "more").mkdir(parents=True)
    old(make_pdf(day / "forms.pdf", pages=2))
    Image.new("RGB", (400, 300), "white").save(day / "photo.jpg")
    old(day / "photo.jpg")
    (day / "notes.txt").write_text("not a form")
    old(day / "notes.txt")
    (day / "~$lock.pdf").write_bytes(b"%PDF-1.4 tmp")
    old(day / "~$lock.pdf")
    (day / "empty.pdf").write_bytes(b"")
    old(day / "empty.pdf")
    (day / "more" / "copy.pdf").write_bytes((day / "forms.pdf").read_bytes())
    old(day / "more" / "copy.pdf")
    make_pdf(day / "scanning-now.pdf", pages=1)  # just written
    return root


@pytest.fixture
def client(tmp_path, inbox, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    monkeypatch.setenv("CPU_WORKERS", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("VLM_MODE", "live")
    monkeypatch.setenv("INBOX_DIR", str(inbox))
    monkeypatch.setenv("SCAN_FOLDER_HOST", HOST)
    get_settings.cache_clear()
    from vera.api.main import app

    with TestClient(app) as c:
        svc = app.state.batches
        svc.dpi, svc.orient = 60, False
        yield c, svc
    get_settings.cache_clear()


def test_resolve_accepts_host_paths_and_subfolders(inbox):
    day = inbox / "2026-10-22"
    assert resolve(inbox, HOST, rf"{HOST}\2026-10-22") == day.resolve()
    assert resolve(inbox, HOST, '"C:/Users/lab/Scans/2026-10-22/"') == day.resolve()
    assert resolve(inbox, HOST, "2026-10-22") == day.resolve()
    assert resolve(inbox, HOST, "") == inbox.resolve()
    assert (
        resolve(inbox, HOST, "file:///C:/Users/lab/Scans/2026-10-22") == day.resolve()
    )
    with pytest.raises(InboxError, match="אין גישה"):
        resolve(inbox, HOST, r"D:\Elsewhere")
    # web addresses are scanned by inbox_url before resolve; a network share
    # can't be reached from the container
    with pytest.raises(InboxError, match="תיקיית רשת"):
        resolve(inbox, HOST, r"\\server\scans")
    with pytest.raises(InboxError):
        resolve(inbox, HOST, "../../etc")
    with pytest.raises(InboxError, match="לא קיימת"):
        resolve(inbox, HOST, "nope")


def test_resolve_with_a_whole_drive_mounted(tmp_path):
    (tmp_path / "Users" / "lab" / "Downloads").mkdir(parents=True)
    got = resolve(tmp_path, "C:\\", r"C:\Users\lab\Downloads")
    assert got == (tmp_path / "Users" / "lab" / "Downloads").resolve()
    assert resolve(tmp_path, "C:\\", "C:") == tmp_path.resolve()


def test_preview_picks_only_relevant_files(client):
    c, _ = client
    r = c.get(
        "/api/inbox", params={"path": rf"{HOST}\2026-10-22", "recursive": True}
    ).json()
    why = {f["path"]: f["reason"] for f in r["files"]}
    assert why == {
        "forms.pdf": "ok",
        "photo.jpg": "ok",
        "notes.txt": "not_document",
        "~$lock.pdf": "system",
        "empty.pdf": "empty",
        "more/copy.pdf": "duplicate",  # same bytes as forms.pdf
        "scanning-now.pdf": "writing",
    }
    assert r["relevant"] == 2 and r["skipped"] == 5
    assert c.get("/api/inbox", params={"path": r"D:\x"}).status_code == 422


def test_import_creates_a_batch_and_skips_next_time(client):
    c, svc = client
    svc._readers = Readers(FakeReader("m1"), FakeReader("m2"), "prompt")
    r = c.post("/api/inbox/import", json={"path": "2026-10-22"}).json()
    assert r["batch_id"] and r["job_id"]
    assert sorted(a["filename"] for a in r["added"]) == ["forms.pdf", "photo.jpg"]
    b = wait_done(c, r["batch_id"])
    assert b["counts"]["pages"] == {"done": 3}
    again = c.get("/api/inbox", params={"path": "2026-10-22"}).json()
    assert again["relevant"] == 0
    assert {f["reason"] for f in again["files"] if f["path"] == "forms.pdf"} == {
        "duplicate"
    }
    nothing = c.post("/api/inbox/import", json={"path": "2026-10-22"}).json()
    assert nothing["batch_id"] is None


def test_without_a_key_pages_fail_fast_with_the_reason(client):
    c, svc = client
    o = c.get("/api/overview").json()
    assert "ANTHROPIC_API_KEY" in o["vlm_problem"]
    assert o["scan_folder"] == HOST
    r = c.post("/api/inbox/import", json={"path": "2026-10-22"}).json()
    b = wait_done(c, r["batch_id"])
    assert b["counts"]["pages"] == {"failed": 3}
    assert "ANTHROPIC_API_KEY" in b["documents"][0]["errors"][0]


def test_retry_reads_only_the_failed_pages_once(client):
    c, svc = client
    r = c.post("/api/inbox/import", json={"path": "2026-10-22"}).json()
    batch_id = r["batch_id"]
    assert wait_done(c, batch_id)["counts"]["pages"] == {"failed": 3}

    svc._readers = Readers(FakeReader("m1"), FakeReader("m2"), "prompt")
    again = c.post(f"/api/batches/{batch_id}/retry-failed").json()
    assert again["requeued"] == 3 and again["job_id"]
    b = wait_done(c, batch_id)
    assert b["counts"]["pages"] == {"done": 3}
    assert len(b["submissions"]) == 3  # no duplicate rows

    none_left = c.post(f"/api/batches/{batch_id}/retry-failed").json()
    assert none_left == {"requeued": 0, "job_id": None}
    assert c.post("/api/batches/nope/retry-failed").status_code == 404
