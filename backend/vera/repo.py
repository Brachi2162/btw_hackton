"""Database access.

Built on SQLModel/SQLAlchemy, so the database is chosen by DATABASE_URL:
SQLite in the hackathon, Postgres in production, same code. (Postgres also
needs its driver installed, e.g. `psycopg[binary]`.)
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, text


def create_db_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        db_file = url.removeprefix("sqlite:///")
        if db_file and db_file != ":memory:":
            Path(db_file).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    return create_engine(url, pool_pre_ping=True)


class Repository:
    def __init__(self, url: str) -> None:
        self.engine = create_db_engine(url)

    def init_schema(self) -> None:
        # Table models (vera/models.py, spec section 14) register on
        # SQLModel.metadata when imported; create whatever is registered.
        SQLModel.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine) as s:
            yield s

    def ping(self) -> bool:
        with self.session() as s:
            return s.exec(text("SELECT 1")).one()[0] == 1

    def close(self) -> None:
        self.engine.dispose()
