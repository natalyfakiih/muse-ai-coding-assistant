"""Database engine, session factory and the JSONB/JSON column type.

Postgres is the target (Section 7) — JSONB columns carry the Specification
content and the Assignment rubric. SQLite is supported as a variant purely so
the project boots with zero infrastructure; the column type below picks the
right one per dialect.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import JSON, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# JSONB on Postgres, plain JSON everywhere else.
JSONColumn = JSON().with_variant(JSONB(), "postgresql")

_connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_connect_args,
    pool_pre_ping=True,
    future=True,
)

if settings.DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover
        """WAL plus a busy timeout so the llm_calls writer and a request's own
        transaction don't deadlock each other. Postgres needs none of this."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401  (registers mappers)

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """A one-step migration for added columns.

    `create_all` only creates missing TABLES, so adding a column to an existing
    model leaves a working database that crashes on the next query. This walks
    the models and ALTERs in anything absent, so iterating on the schema doesn't
    mean losing your test data every time. Anything more involved than an added
    nullable/defaulted column needs a real migration tool.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                column_type = column.type.compile(engine.dialect)
                default = ""
                if column.default is not None and getattr(column.default, "is_scalar", False):
                    default = f" DEFAULT {column.default.arg!r}"
                elif column.default is not None:
                    # JSON/callable defaults: give existing rows an empty value
                    # rather than NULL, which the models don't expect.
                    sample = column.default.arg({}) if callable(column.default.arg) else None
                    if isinstance(sample, dict):
                        default = " DEFAULT '{}'"
                    elif isinstance(sample, list):
                        default = " DEFAULT '[]'"
                connection.execute(text(
                    f'ALTER TABLE {table.name} ADD COLUMN {column.name} {column_type}{default}'
                ))
