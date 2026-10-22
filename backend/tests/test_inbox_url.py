"""Scanner folder by web address: a real local HTTP server publishes a folder
as a file list, as a scanner or file server would. Synthetic files only."""

import functools
import os
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.conftest import make_pdf
from tests.test_api import FakeReader, wait_done
from vera.batch import Readers
from vera.config import get_settings


def yesterday(path):
    t = time.time() - 36 * 3600
    os.utime(path, (t, t))
    return path


def settled(path):
    t = time.time() - 600  # written ten minutes ago, still today
    os.utime(path, (t, t))
    return path


@pytest.fixture
def served(tmp_path):
    root = tmp_path / "scans"
    (root / "fax").mkdir(parents=True)
    settled(make_pdf(root / "today.pdf", pages=2))
    yesterday(make_pdf(root / "old.pdf", pages=1))
    Image.new("RGB", (300, 400), "white").save(root / "fax" / "photo.png")
    settled(root / "fax" / "photo.png")
    (root / "notes.txt").write_text("not a form")
    settled(root / "notes.txt")
    (root / "fax" / "same.pdf").write_bytes((root / "today.pdf").read_bytes())
    settled(root / "fax" / "same.pdf")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    monkeypatch.setenv("CPU_WORKERS", "1")
    get_settings.cache_clear()
    from vera.api.main import app

    with TestClient(app) as c:
        svc = app.state.batches
        svc.dpi, svc.orient = 60, False
        svc._readers = Readers(FakeReader("m1"), FakeReader("m2"), "prompt")
        yield c
    get_settings.cache_clear()


def test_a_folder_address_keeps_only_todays_new_documents(client, served):
    _, url = served
    r = client.get("/api/inbox", params={"path": url, "today": "true"})
    assert r.status_code == 200, r.text
    why = {f["path"]: f["reason"] for f in r.json()["files"]}
    assert why == {
        "fax/photo.png": "ok",
        "fax/same.pdf": "ok",  # first of two identical files, by name
        "today.pdf": "duplicate",
        "old.pdf": "old",
        "notes.txt": "not_document",
    }
    everything = client.get("/api/inbox", params={"path": url}).json()
    assert everything["relevant"] == 3  # old.pdf counts without "today"


def test_importing_from_an_address_then_again(client, served):
    _, url = served
    r = client.post("/api/inbox/import", json={"path": url, "today_only": True}).json()
    assert sorted(a["filename"] for a in r["added"]) == ["photo.png", "same.pdf"]
    b = wait_done(client, r["batch_id"])
    assert b["counts"]["pages"] == {"done": 3}
    again = client.post("/api/inbox/import", json={"path": url, "today_only": True})
    assert again.json()["batch_id"] is None  # nothing new


def test_links_out_of_the_folder_are_not_followed(client, served):
    root, url = served
    (root / "fax" / "index.html").write_text(
        '<a href="photo.png">ok</a> <a href="http://example.com/x.pdf">out</a>'
        ' <a href="../today.pdf">up</a> <a href="?C=N;O=D">sort</a>'
        ' <a href="gone.pdf">missing</a>'
    )
    r = client.get("/api/inbox", params={"path": url + "fax"})  # no slash
    files = {f["path"]: f["reason"] for f in r.json()["files"]}
    assert files == {"photo.png": "ok", "gone.pdf": "fetch_failed"}


@pytest.mark.parametrize(
    "path,says",
    [
        ("nope/", "404"),
        ("today.pdf", "לא רשימת קבצים"),
    ],
)
def test_bad_addresses_say_why(client, served, path, says):
    _, url = served
    r = client.get("/api/inbox", params={"path": url + path})
    assert r.status_code == 422 and says in r.json()["detail"]


def test_a_server_that_is_down_says_so(client):
    r = client.get("/api/inbox", params={"path": "http://127.0.0.1:9/scans/"})
    assert r.status_code == 422 and "אין גישה" in r.json()["detail"]
