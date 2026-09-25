"""Database engine/session (SQLAlchemy 2.0 — same pattern as the existing
EDUnation projects; swap ASSESS_DB_URL to Postgres for production)."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    url = get_settings().db_url
    if url.startswith("sqlite"):
        Path(url.split("///")[-1]).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_connection, _record):  # pragma: no cover
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()
        return engine
    return create_engine(url, pool_pre_ping=True)


class Database:
    def __init__(self) -> None:
        self.engine = _make_engine()
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

    def init(self) -> None:
        from . import models  # noqa: F401 — register tables
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session_scope(self) -> Generator[Session, None, None]:
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


_database: Database | None = None


def get_database() -> Database:
    global _database
    if _database is None:
        _database = Database()
        _database.init()
    return _database


def db() -> Database:
    return get_database()
