"""Watching a folder (D-017): new forms in it go to review by themselves.
Synthetic files only."""

import os
import time

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_pdf
from tests.test_api import FakeReader, wait_done
from vera.batch import Readers
from vera.config import get_settings
from vera.watch import FolderWatch

HOST = r"C:\Users\lab\Scans"


def settled(path):
    """Written a while ago today: not "still being written"."""
    t = time.time() - 120
    os.utime(path, (t, t))
    return path


@pytest.fixture
def client(tmp_path, monkeypatch):
    root = tmp_path / "inbox"
    (root / "today").mkdir(parents=True)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    monkeypatch.setenv("CPU_WORKERS", "1")
    monkeypatch.setenv("INBOX_DIR", str(root))
    monkeypatch.setenv("SCAN_FOLDER_HOST", HOST)
    get_settings.cache_clear()
    from vera.api.main import app

    with TestClient(app) as c:
        svc = app.state.batches
        svc.dpi, svc.orient = 60, False
        svc._readers = Readers(FakeReader("m1"), FakeReader("m2"), "prompt")
        yield c, app, root / "today"
    get_settings.cache_clear()


def test_new_forms_in_a_watched_folder_go_to_review_by_themselves(client, tmp_path):
    c, app, folder = client
    watch = app.state.watch
    r = c.put("/api/inbox/watch", json={"folder": rf"{HOST}\today", "enabled": True})
    assert r.status_code == 200 and r.json()["enabled"]
    assert c.portal.call(watch.tick) is None  # an empty folder: nothing to do

    settled(make_pdf(folder / "first.pdf", pages=2))
    first = c.portal.call(watch.tick)
    assert [a["filename"] for a in first["added"]] == ["first.pdf"]
    b = wait_done(c, first["batch_id"])
    assert b["counts"]["pages"] == {"done": 2} and b["name"].startswith("מעקב תיקייה")

    settled(make_pdf(folder / "second.pdf", pages=1, text=False))
    second = c.portal.call(watch.tick)
    assert second["batch_id"] == first["batch_id"]  # one batch per day
    assert wait_done(c, first["batch_id"])["counts"]["pages"] == {"done": 3}

    assert c.portal.call(watch.tick) is None  # never the same file twice
    status = c.get("/api/inbox/watch").json()
    assert status["added_today"] == 2 and status["last_check"]

    # the setting survives a restart
    assert FolderWatch(app.state.batches).get()["enabled"] is True
    off = c.put("/api/inbox/watch", json={"folder": "", "enabled": False})
    assert off.json()["enabled"] is False


def test_a_folder_the_server_cant_read_is_refused(client):
    c, _, _ = client
    r = c.put("/api/inbox/watch", json={"folder": r"D:\Elsewhere", "enabled": True})
    assert r.status_code == 422
    assert c.get("/api/inbox/watch").json()["enabled"] is False
