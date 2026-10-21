"""Background job interface.

Hackathon: InProcessJobQueue (asyncio tasks inside the API process; CPU-heavy
work goes to a process pool so it doesn't block the event loop).
Production: a Redis/RQ (or Celery) class with the same methods, selected by
JOB_BACKEND in vera/services.py. Callers only see `JobQueue`.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any, Literal, Protocol

JobState = Literal["queued", "running", "done", "failed"]


@dataclass
class JobStatus:
    id: str
    state: JobState = "queued"
    result: Any = None
    error: str | None = None


class JobQueue(Protocol):
    async def submit(self, fn: Callable[..., Awaitable[Any]], *args: Any) -> str:
        """Schedule an async job. Returns a job id."""
        ...

    async def run_cpu(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Run a CPU-bound, picklable function off the event loop."""
        ...

    def status(self, job_id: str) -> JobStatus | None: ...
    async def wait(self, job_id: str) -> JobStatus: ...
    async def shutdown(self) -> None: ...


class InProcessJobQueue:
    def __init__(self, max_concurrent: int = 4, cpu_workers: int | None = None):
        self._sem = asyncio.Semaphore(max_concurrent)
        self._pool = ProcessPoolExecutor(max_workers=cpu_workers or None)
        self._jobs: dict[str, JobStatus] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def submit(self, fn: Callable[..., Awaitable[Any]], *args: Any) -> str:
        job_id = uuid.uuid4().hex
        self._jobs[job_id] = JobStatus(id=job_id)
        self._tasks[job_id] = asyncio.create_task(self._run(job_id, fn, args))
        return job_id

    async def _run(self, job_id: str, fn: Callable[..., Awaitable[Any]], args):
        job = self._jobs[job_id]
        async with self._sem:
            job.state = "running"
            try:
                job.result = await fn(*args)
                job.state = "done"
            except Exception as exc:  # job failure must not kill the server
                job.error = f"{type(exc).__name__}: {exc}"
                job.state = "failed"

    async def run_cpu(self, fn: Callable[..., Any], *args: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, fn, *args)

    def status(self, job_id: str) -> JobStatus | None:
        return self._jobs.get(job_id)

    async def wait(self, job_id: str) -> JobStatus:
        task = self._tasks.get(job_id)
        if task is not None:
            await asyncio.shield(task)
        return self._jobs[job_id]

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._pool.shutdown(wait=False, cancel_futures=True)
