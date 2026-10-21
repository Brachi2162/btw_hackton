"""Wires interfaces to implementations, based on Settings.

This is the only place that knows which concrete class backs Storage and
JobQueue. To move to production infra: add a class (e.g. S3Storage,
RQJobQueue), register it below, and change the env var. Pipeline, API and
CLI code stay untouched.
"""

from dataclasses import dataclass

from vera.config import Settings
from vera.jobs import InProcessJobQueue, JobQueue
from vera.repo import Repository
from vera.storage import LocalStorage, Storage


@dataclass
class Services:
    settings: Settings
    storage: Storage
    jobs: JobQueue
    repo: Repository

    async def close(self) -> None:
        await self.jobs.shutdown()
        self.repo.close()


def build_storage(s: Settings) -> Storage:
    backends = {"local": lambda: LocalStorage(s.data_dir / "files")}
    return backends[s.storage_backend]()


def build_jobs(s: Settings) -> JobQueue:
    backends = {
        "inprocess": lambda: InProcessJobQueue(
            max_concurrent=s.max_concurrent_jobs, cpu_workers=s.cpu_workers or None
        ),
    }
    return backends[s.job_backend]()


def build_services(s: Settings) -> Services:
    repo = Repository(s.resolved_database_url)
    repo.init_schema()
    return Services(settings=s, storage=build_storage(s), jobs=build_jobs(s), repo=repo)
