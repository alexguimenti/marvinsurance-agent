"""Where a claim's progress is saved after every step: Postgres on Supabase, a SQLite file everywhere else.
Synchronous on purpose: async Postgres connections don't work with Windows' default event loop.

A saver holds a lock around every read and write, so each claim run gets a saver of its own. On Postgres they all
share the process's one pool, and wait for a connection rather than fail."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import Settings
from app.db import CONNECTION_WAIT_S, SCHEMA, row_level_security

CHECKPOINT_TABLES = ("checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes")
MAX_CONNECTIONS = 2  # with the agent's 2 for records, the 4 a process may use


def _search_path(connection: psycopg.Connection) -> None:
    connection.execute(f"SET search_path TO {SCHEMA}")


def checkpoint_file(settings: Settings) -> str:
    path = Path(settings.db_path)
    return str(path.with_name(f"{path.stem}.checkpoints.db"))


class Checkpoints:
    """Hands out savers: one for each claim run, over the pool on Postgres, over a connection of its own on SQLite."""

    def __init__(self, pool: ConnectionPool | None = None, file: str | None = None):
        self.pool, self.file = pool, file

    @contextmanager
    def saver(self) -> Iterator[BaseCheckpointSaver]:
        if self.pool is not None:
            yield PostgresSaver(self.pool)
            return
        connection = sqlite3.connect(self.file, check_same_thread=False, timeout=CONNECTION_WAIT_S)
        try:
            yield SqliteSaver(connection)
        finally:
            connection.close()


@contextmanager
def make_checkpoints(settings: Settings) -> Iterator[Checkpoints]:
    if settings.database_url:
        with ConnectionPool(
            settings.database_url,
            min_size=MAX_CONNECTIONS,  # opened now: a new one to Supabase takes seconds
            max_size=MAX_CONNECTIONS,
            timeout=CONNECTION_WAIT_S,
            open=True,
            configure=_search_path,
            kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},  # what PostgresSaver needs
        ) as pool:
            pool.wait(timeout=60)
            PostgresSaver(pool).setup()  # creates the tables the first time, and does nothing after that
            with pool.connection() as connection:
                for statement in row_level_security(CHECKPOINT_TABLES):  # checkpoints hold each claim's full text
                    connection.execute(statement)
            yield Checkpoints(pool=pool)
    else:
        file = checkpoint_file(settings)
        connection = sqlite3.connect(file)
        try:
            SqliteSaver(connection).setup()  # and WAL, so savers on other connections can read while one writes
        finally:
            connection.close()
        yield Checkpoints(file=file)


def clear_checkpoints(settings: Settings) -> None:
    """Forget every thread, so a reseeded claim cannot pick up an old conversation."""
    if settings.database_url:
        with psycopg.connect(settings.database_url, autocommit=True, prepare_threshold=0) as connection:
            _search_path(connection)
            for table in CHECKPOINT_TABLES[1:]:
                if connection.execute("SELECT to_regclass(%s)", (f"{SCHEMA}.{table}",)).fetchone()[0]:
                    connection.execute(f"DELETE FROM {table}")
        return
    connection = sqlite3.connect(checkpoint_file(settings))
    try:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in {"checkpoints", "writes"} & tables:
            connection.execute(f"DELETE FROM {table}")
        connection.commit()
    finally:
        connection.close()
