"""The records: one set of tables and queries for SQLite and Postgres, through SQLAlchemy Core."""
import time
from datetime import datetime, timezone
from typing import Iterable, Mapping

from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    Float,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    event,
    func,
    make_url,
    text,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import DBAPIError

from app.config import Settings

SCHEMA = "undercover"  # set on each Postgres connection, so the queries stay the same on SQLite

metadata = MetaData()


def _timestamp(name: str, *, default: bool = True) -> Column:
    return Column(name, DateTime(timezone=True), server_default=func.now() if default else None)


claims = Table(
    "claims", metadata,
    Column("claim_id", Text, primary_key=True),
    Column("status", Text, nullable=False),
    Column("payload", Text, nullable=False),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("last_error", Text),
    Column("run_id", Integer),
    Column("thread_id", Text),
)

decisions = Table(
    "decisions", metadata,
    Column("claim_id", Text, primary_key=True),
    Column("decision", Text, nullable=False),
    Column("payout", Float, nullable=False),
    Column("calculation", Text, nullable=False),
    Column("rationale", Text, nullable=False),
    Column("citations", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("manual_version", Text),
    Column("wording_version", Text),
    _timestamp("created_at"),
)

trace_events = Table(
    "trace_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", Text, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("kind", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("run_id", Integer),
    _timestamp("created_at"),
)

payments = Table(
    "payments", metadata,
    Column("payment_id", Text, primary_key=True),
    Column("claim_id", Text, nullable=False, unique=True),
    Column("amount", Float, nullable=False),
    Column("status", Text, nullable=False),
    Column("approved_by", Text),
    _timestamp("approved_at", default=False),
    _timestamp("paid_at", default=False),
    _timestamp("created_at"),
    _timestamp("updated_at"),
)

runs = Table(
    "runs", metadata,
    Column("run_id", Integer, primary_key=True, autoincrement=True),
    Column("status", Text, nullable=False),
    Column("cap", Integer),
    Column("scope", Text),
    Column("model", Text, nullable=False),
    Column("summary", Text),
    Column("error", Text),
    Column("usage", Text),
    _timestamp("started_at"),
    _timestamp("finished_at", default=False),
)
# One run at a time holds across processes, whoever asks.
Index("one_running_run", runs.c.status, unique=True,
      sqlite_where=runs.c.status == "running", postgresql_where=runs.c.status == "running")

todo_items = Table(
    "todo_items", metadata,
    Column("item_id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, nullable=False),
    Column("position", Integer, nullable=False),
    Column("title", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("claim_id", Text),
    Column("note", Text),
)

run_events = Table(
    "run_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("kind", Text, nullable=False),
    Column("content", Text, nullable=False),
    _timestamp("created_at"),
)


def make_engine(settings: Settings, max_connections: int) -> Engine:
    """A pool of at most max_connections. Operations open short transactions, so a few go a long way."""
    if settings.database_url:
        return _postgres_engine(settings.database_url, max_connections)
    engine = create_engine(
        f"sqlite:///{settings.db_path}",
        pool_size=max_connections,
        max_overflow=0,
        connect_args={"timeout": 10, "check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _wal(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA journal_mode=WAL")

    return engine


def _postgres_engine(database_url: str, max_connections: int) -> Engine:
    url = make_url(database_url).set(drivername="postgresql+psycopg")
    engine = create_engine(url, pool_size=max_connections, max_overflow=0, pool_pre_ping=True, pool_recycle=300)

    @event.listens_for(engine, "connect")
    def _search_path(dbapi_connection, _record):
        dbapi_connection.autocommit = True
        dbapi_connection.execute(f"SET search_path TO {SCHEMA}")
        dbapi_connection.autocommit = False

    return engine


def row_level_security(table_names: Iterable[str]) -> list[str]:
    """Without this, the project's public API keys could reach the tables. Our owner role is not affected."""
    return [f"ALTER TABLE {SCHEMA}.{name} ENABLE ROW LEVEL SECURITY" for name in table_names]


def init_schema(engine: Engine) -> None:
    """Create what is missing. Two processes starting together can race on a table, so a loser tries again."""
    for attempt in range(5):
        try:
            if engine.dialect.name == "postgresql":
                with engine.begin() as conn:
                    conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
            metadata.create_all(engine)
            if engine.dialect.name == "postgresql":
                with engine.begin() as conn:
                    for statement in row_level_security(metadata.tables):
                        conn.execute(text(statement))
            return
        except DBAPIError:
            if attempt == 4:
                raise
            time.sleep(0.2 * (attempt + 1))


def insert_or_ignore(bind, table: Table):
    """An INSERT that leaves an existing row alone, on either database."""
    dialect = postgresql if bind.dialect.name == "postgresql" else sqlite
    return dialect.insert(table).on_conflict_do_nothing()


def plain(row: Mapping) -> dict:
    """A row as JSON-ready values. SQLite's timestamps come back naive, and are UTC."""
    return {key: iso(value) if isinstance(value, datetime) else value for key, value in row.items()}


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def drop_tables(engine: Engine) -> None:
    metadata.drop_all(engine)
