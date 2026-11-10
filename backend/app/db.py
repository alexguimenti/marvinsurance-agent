"""The records: one set of tables and queries for SQLite and Postgres, through SQLAlchemy Core."""
import threading
import time
from datetime import datetime, timezone
from typing import Iterable, Mapping

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.pool import NullPool

from app.config import Settings

CONNECTION_WAIT_S = 600  # claim agents share a process's few connections and wait for one rather than fail
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
    Column("submitted_on", Text),  # the dispatcher works the oldest first
    Column("copy_of", Text),  # an evaluation's copy: the claim it copies. Copies never show in the queue
    Column("evaluation_id", Integer),
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
    Column("approval_limit", Float),  # the limit in force when it was recorded: a new limit applies to later decisions
    _timestamp("created_at"),
)

documents = Table(
    "documents", metadata,
    Column("document", Text, primary_key=True),  # policy_wording or claims_manual
    Column("version", Text, nullable=False),  # from the document's front matter
    Column("source", Text, nullable=False),  # JSON: the system it comes from, its owner and when it was reviewed
    Column("text", Text, nullable=False),  # the document without its front matter
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
    Column("hold_reason", Text),  # why it waits for a sign-off: over_limit, or gateway_refused
    _timestamp("approved_at", default=False),
    _timestamp("paid_at", default=False),
    _timestamp("created_at"),
    _timestamp("updated_at"),
)

runs = Table(
    "runs", metadata,
    Column("run_id", Integer, primary_key=True, autoincrement=True),
    Column("kind", Text, nullable=False),  # dispatch or evaluation
    Column("status", Text, nullable=False),  # running, finished, or stopped when its process went
    Column("workers", Integer, nullable=False),  # N: claim agents at once
    Column("model", Text, nullable=False),
    Column("reasoning_effort", Text, nullable=False),  # "none" when none was sent
    Column("eval_set", Text),
    Column("tiny", Boolean),
    Column("config_version", Integer),  # the configuration the run used
    Column("round_id", Integer),  # set on the evaluations an improver round asked for
    Column("error", Text),
    _timestamp("started_at"),
    _timestamp("finished_at", default=False),
)
# One run at a time holds across processes, whoever asks.
Index("one_running_run", runs.c.status, unique=True,
      sqlite_where=runs.c.status == "running", postgresql_where=runs.c.status == "running")

settings_table = Table(
    "settings", metadata,
    Column("name", Text, primary_key=True),
    Column("value", Text, nullable=False),  # JSON
    Column("changed_by", Text, nullable=False),  # a handler id, or "seed"
    Column("changed_at", DateTime(timezone=True), nullable=False),
)

settings_history = Table(
    "settings_history", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("value", Text, nullable=False),
    Column("changed_by", Text, nullable=False),
    Column("changed_at", DateTime(timezone=True), nullable=False),
)

config_versions = Table(
    "config_versions", metadata,
    Column("version", Integer, primary_key=True),
    Column("instructions", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("reasoning_effort", Text, nullable=False),  # "default" when none is sent
    Column("summary", Text, nullable=False),  # what changed from the version before
    Column("reason", Text, nullable=False),
    Column("proposed_by", Text, nullable=False),  # "seed", "improver", or a handler
    Column("approved_by", Text, nullable=False),
    Column("approved_at", DateTime(timezone=True)),
    Column("round_id", Integer),
    _timestamp("created_at"),
)

recipe_versions = Table(
    "recipe_versions", metadata,
    Column("version", Integer, primary_key=True),
    Column("recipe", Text, nullable=False),
    Column("summary", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("proposed_by", Text, nullable=False),
    Column("approved_by", Text, nullable=False),
    Column("approved_at", DateTime(timezone=True)),
    Column("round_id", Integer),
    _timestamp("created_at"),
)

current_version = Table(
    "current_version", metadata,
    Column("kind", Text, primary_key=True),  # config or recipe
    Column("version", Integer, nullable=False),
)

rounds = Table(
    "rounds", metadata,
    Column("round_id", Integer, primary_key=True, autoincrement=True),
    Column("stage", Text, nullable=False),  # first_evaluations, improving, awaiting_change, evaluating, verdict, awaiting_recipe, finished, stopped
    Column("progress", Text),  # "2 of 3" while the evaluations run
    Column("open", Boolean, nullable=False),  # one round at a time
    Column("recipe_version", Integer, nullable=False),  # the recipe it followed
    Column("settings", Text, nullable=False),  # JSON: the improvement settings as they stood when it started
    Column("thread_id", Text, nullable=False),  # the improver's own thread, new for each round
    Column("version_tried", Integer),
    Column("reason", Text),  # the improver's reason for the version it tried
    Column("verdict", Text),  # kept or rolled_back
    Column("why", Text),
    Column("before", Text),  # JSON: the held-out scores before
    Column("after", Text),  # JSON: the held-out scores after
    Column("started_by", Text, nullable=False),
    Column("stopped_by", Text),
    Column("error", Text),
    _timestamp("started_at"),
    _timestamp("finished_at", default=False),
)
Index("one_open_round", rounds.c.open, unique=True, sqlite_where=rounds.c.open, postgresql_where=rounds.c.open)

proposals = Table(
    "proposals", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("round_id", Integer, nullable=False),
    Column("kind", Text, nullable=False),  # change or recipe
    Column("payload", Text, nullable=False),  # JSON: what is proposed
    Column("reason", Text, nullable=False),  # the improver's
    Column("status", Text, nullable=False),  # waiting, approved or rejected
    Column("decided_by", Text),
    Column("decided_at", DateTime(timezone=True)),
    Column("decision_reason", Text),  # a rejection's reason goes back to the improver
    _timestamp("created_at"),
)

run_claims = Table(
    "run_claims", metadata,
    Column("run_id", Integer, primary_key=True),
    Column("claim_id", Text, primary_key=True),
    Column("position", Integer, nullable=False),
    _timestamp("started_at", default=False),
    _timestamp("finished_at", default=False),
)

model_calls = Table(
    "model_calls", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", Text, nullable=False),
    Column("run_id", Integer),  # none outside a run, as when a sign-off is paid
    Column("round_id", Integer),  # set on the improver's own calls
    Column("model", Text, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("reasoning_tokens", Integer, nullable=False),
    Column("cost_usd", Float),  # as OpenRouter reports it; none when it doesn't
    _timestamp("created_at"),
)

work_spans = Table(
    "work_spans", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", Text, nullable=False),
    Column("run_id", Integer),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("seconds", Float, nullable=False),
)

# The answer key, seeded from the labelled sets. No claim operation reads these, and the agent never does: the backend
# scores each evaluation from them. eval_sets says which claims an evaluation copies, and holds no decision.
held_out_claims = Table(
    "held_out_claims", metadata,
    Column("claim_id", Text, primary_key=True),
    Column("payload", Text, nullable=False),
)

eval_sets = Table(
    "eval_sets", metadata,
    Column("eval_set", Text, primary_key=True),
    Column("claim_id", Text, primary_key=True),
    Column("tiny", Boolean, nullable=False),
)

answer_key = Table(
    "answer_key", metadata,
    Column("eval_set", Text, primary_key=True),
    Column("claim_id", Text, primary_key=True),
    Column("decision", Text, nullable=False),
    Column("payout", Float, nullable=False),
    Column("reason", Text, nullable=False),
    Column("reference", Text, nullable=False),  # the part of the policy wording the handlers relied on
)

# The judge's grade for each claim of an evaluation. A null score is a claim that could not be graded.
judge_grades = Table(
    "judge_grades", metadata,
    Column("run_id", Integer, primary_key=True),
    Column("claim_id", Text, primary_key=True),
    Column("score", Float),  # 0 (F) to 3 (A), Jev's own score
    Column("letter", Text),  # the most likely level
    Column("same_grounds", Float),  # a probability
    Column("same_section", Float),  # a probability
    Column("cost_usd", Float, nullable=False, server_default="0"),
    Column("error", Text),
)

# Tables of the queue agent, gone with it. A reseed drops them from an older database.
LEGACY_TABLES = ("todo_items", "run_events")


def make_engine(settings: Settings, max_connections: int) -> Engine:
    """A pool of at most max_connections, where every statement commits by itself: BEGIN, COMMIT and a ping per
    checkout would make each query four trips to Supabase. So each statement must be safe on its own, and an
    operation must be safe to repeat if it stops between two. One statement, with its constraints, is atomic."""
    if settings.database_url and settings.on_lambda:
        return _lambda_engine(settings.database_url)
    if settings.database_url:
        return _postgres_engine(settings.database_url, max_connections).execution_options(isolation_level="AUTOCOMMIT")
    engine = create_engine(
        f"sqlite:///{settings.db_path}",
        pool_size=max_connections,
        max_overflow=0,
        pool_timeout=CONNECTION_WAIT_S,
        connect_args={"timeout": 10, "check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _wal(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA journal_mode=WAL")

    return engine.execution_options(isolation_level="AUTOCOMMIT")


def warm_up(engine: Engine, connections: int) -> None:
    """Open the pool's connections now. A new one to Supabase takes seconds, too long to find out during a sign-off."""
    start = threading.Barrier(connections)

    def open_one() -> None:
        with engine.connect():
            start.wait(timeout=60)  # held until all are open, so each opens a connection of its own

    threads = [threading.Thread(target=open_one) for _ in range(connections)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def _lambda_engine(database_url: str) -> Engine:
    """Lambda starts a copy of the function for each request that arrives while the others are busy, so connections kept
    by each copy would soon reach the pooler's limit: one is opened for each request and none is kept (NullPool). The
    pooler (Supabase's transaction pooler, port 6543) keeps no prepared statements and no `SET` between statements, so
    prepared statements are off and every statement names the schema."""
    url = make_url(database_url).set(drivername="postgresql+psycopg")
    engine = create_engine(url, poolclass=NullPool, connect_args={"prepare_threshold": None})
    return engine.execution_options(isolation_level="AUTOCOMMIT", schema_translate_map={None: SCHEMA})


def _postgres_engine(database_url: str, max_connections: int) -> Engine:
    url = make_url(database_url).set(drivername="postgresql+psycopg")
    engine = create_engine(url, pool_size=max_connections, max_overflow=0, pool_recycle=1800, pool_timeout=CONNECTION_WAIT_S)

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
    with engine.begin() as conn:
        for name in LEGACY_TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
