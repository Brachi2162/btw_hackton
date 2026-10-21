"""Runtime settings, read from environment variables (and .env).

Which implementation backs each interface (storage, job queue, database) is
chosen here, not in code. See vera/services.py.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/vera/config.py -> repo root (in Docker: /app/vera -> /).
_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Where uploads, page images, work files and the VLM cache live.
    data_dir: Path = Path("data")

    # Any SQLAlchemy URL. Empty = SQLite file inside data_dir.
    # Production example: postgresql+psycopg://user:pass@host/vera
    database_url: str = ""

    storage_backend: Literal["local"] = "local"
    job_backend: Literal["inprocess"] = "inprocess"

    # Process pool size for CPU-bound work (OpenCV, OCR). 0 = os.cpu_count().
    cpu_workers: int = 0
    # Max jobs running at once (also bounds concurrent VLM calls per batch).
    max_concurrent_jobs: int = 4

    # Rules and prompt templates (spec 10, 8.3), at the repo root. In Docker
    # they are mounted at /config and /prompts, which is the same path.
    config_dir: Path = _REPO_ROOT / "config"
    prompts_dir: Path = _REPO_ROOT / "prompts"

    vlm_mode: Literal["live", "replay"] = "live"
    anthropic_api_key: str = ""

    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'vera.db').as_posix()}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
