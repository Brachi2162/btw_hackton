import asyncio

import pytest
from fastapi.testclient import TestClient

from vera.config import Settings, get_settings
from vera.jobs import InProcessJobQueue
from vera.repo import Repository
from vera.services import build_services
from vera.storage import LocalStorage

# --- Storage ---


def test_local_storage_roundtrip(tmp_path):
    st = LocalStorage(tmp_path)
    st.put("batch1/page1.png", b"abc")
    assert st.exists("batch1/page1.png")
    assert st.get("batch1/page1.png") == b"abc"
    st.delete("batch1/page1.png")
    assert not st.exists("batch1/page1.png")


@pytest.mark.parametrize("key", ["../escape", "/abs/path", "a/../../b", ""])
def test_local_storage_rejects_unsafe_keys(tmp_path, key):
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).put(key, b"x")


# --- Jobs ---


async def test_job_runs_and_reports_result():
    q = InProcessJobQueue(max_concurrent=2, cpu_workers=1)

    async def work(x):
        await asyncio.sleep(0)
        return x * 2

    job_id = await q.submit(work, 21)
    status = await q.wait(job_id)
    assert (status.state, status.result) == ("done", 42)
    await q.shutdown()


async def test_failed_job_is_recorded_not_raised():
    q = InProcessJobQueue(cpu_workers=1)

    async def boom():
        raise RuntimeError("bad page")

    status = await q.wait(await q.submit(boom))
    assert status.state == "failed"
    assert "bad page" in status.error
    await q.shutdown()


async def test_run_cpu_uses_process_pool():
    q = InProcessJobQueue(cpu_workers=1)
    assert await q.run_cpu(pow, 2, 10) == 1024
    await q.shutdown()


async def test_concurrency_limit_is_respected():
    q = InProcessJobQueue(max_concurrent=2, cpu_workers=1)
    running = peak = 0

    async def work():
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1

    ids = [await q.submit(work) for _ in range(6)]
    for i in ids:
        await q.wait(i)
    assert peak == 2
    await q.shutdown()


# --- Repository / wiring ---


def test_sqlite_repository(tmp_path):
    repo = Repository(f"sqlite:///{(tmp_path / 'x' / 'v.db').as_posix()}")
    repo.init_schema()
    assert repo.ping()
    repo.close()


def test_default_database_is_sqlite_in_data_dir(tmp_path):
    s = Settings(data_dir=tmp_path, database_url="")
    assert s.resolved_database_url.startswith("sqlite:///")
    assert s.resolved_database_url.endswith("/vera.db")


async def test_build_services_from_settings(tmp_path):
    services = build_services(Settings(data_dir=tmp_path, cpu_workers=1))
    services.storage.put("k", b"v")
    assert (tmp_path / "files" / "k").read_bytes() == b"v"
    assert services.repo.ping()
    await services.close()


def test_health_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CPU_WORKERS", "1")
    get_settings.cache_clear()
    from vera.api.main import app

    with TestClient(app) as client:
        body = client.get("/health").json()
    get_settings.cache_clear()

    assert body["status"] == "ok"
    assert body["database"] is True
    assert body["job_backend"] == "inprocess"
    assert body["storage_backend"] == "local"


def test_existing_database_gets_new_columns(tmp_path):
    """A column added to a model later must not break an older database."""
    from sqlalchemy import inspect, text

    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    repo = Repository(url)
    with repo.engine.begin() as conn:  # an "old" pages table, before vlm_crops
        conn.execute(text("CREATE TABLE pages (id VARCHAR PRIMARY KEY)"))
    repo.init_schema()
    cols = {c["name"] for c in inspect(repo.engine).get_columns("pages")}
    assert {"vlm_crops", "vlm_escalated", "status"} <= cols
    repo.close()
