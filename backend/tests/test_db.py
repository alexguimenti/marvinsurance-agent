import threading

import pytest
from sqlalchemy import insert, inspect, select
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db import claims, init_schema, insert_or_ignore, make_engine, reset_analyses, runs
from tests.fakes import connect


def test_connect_uses_wal(db_path):
    assert connect(db_path).execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_tables_exist_after_init(db_path):
    conn = connect(db_path)
    init_schema(conn.engine)
    init_schema(conn.engine)  # twice is harmless
    assert {"claims", "decisions", "trace_events", "payments", "runs", "todo_items", "run_events"} <= set(
        inspect(conn.engine).get_table_names()
    )


def test_make_engine_caps_connections(db_path):
    engine = make_engine(Settings(_env_file=None, db_path=db_path), max_connections=4)
    assert engine.pool.size() == 4 and engine.pool._max_overflow == 0


def test_insert_or_ignore_keeps_the_first_row(db_path):
    conn = connect(db_path)
    init_schema(conn.engine)
    with conn.engine.begin() as c:
        c.execute(insert_or_ignore(c, claims), {"claim_id": "CLM-1", "status": "waiting", "payload": "first"})
        c.execute(insert_or_ignore(c, claims), {"claim_id": "CLM-1", "status": "decided", "payload": "second"})
    with conn.engine.connect() as c:
        assert c.execute(select(claims.c.status, claims.c.payload)).one() == ("waiting", "first")


def test_two_processes_can_init_together(db_path):
    errors = []

    def start():
        try:
            init_schema(make_engine(Settings(_env_file=None, db_path=db_path), max_connections=1))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=start) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_only_one_run_can_be_running(db_path):
    conn = connect(db_path)
    init_schema(conn.engine)
    with conn.engine.begin() as c:
        c.execute(insert(runs).values(status="finished", model="m"))
        c.execute(insert(runs).values(status="finished", model="m"))
        c.execute(insert(runs).values(status="running", model="m"))
    with pytest.raises(IntegrityError), conn.engine.begin() as c:
        c.execute(insert(runs).values(status="running", model="m"))


def test_reset_leaves_every_claim_waiting(db_path):
    conn = connect(db_path)
    init_schema(conn.engine)
    conn.execute("INSERT INTO claims (claim_id, status, payload, attempts, last_error, run_id) "
                 "VALUES ('CLM-1001', 'needs_handler', '{}', 2, 'boom', 1)")
    conn.execute("INSERT INTO runs (status, model) VALUES ('finished', 'm')")
    conn.execute("INSERT INTO todo_items (run_id, position, title, status) VALUES (1, 1, 't', 'done')")
    conn.execute("INSERT INTO run_events (run_id, seq, kind, content) VALUES (1, 1, 'k', '{}')")
    conn.execute("INSERT INTO payments (payment_id, claim_id, amount, status) VALUES ('P', 'CLM-1001', 1, 'paid')")
    conn.execute("INSERT INTO decisions (claim_id, decision, payout, calculation, rationale, citations, model) "
                 "VALUES ('CLM-1001', 'decline', 0, '[]', 'r', '[]', 'm')")

    reset_analyses(conn.engine)

    for table in ("runs", "todo_items", "run_events", "payments", "decisions", "trace_events"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert tuple(conn.execute("SELECT status, attempts, last_error, run_id FROM claims").fetchone()) == ("waiting", 0, None, None)


def test_decision_cannot_be_written_twice(db_path):
    conn = connect(db_path)
    init_schema(conn.engine)
    sql = ("INSERT INTO decisions (claim_id, decision, payout, calculation, rationale, citations, model) "
           "VALUES ('CLM-1001', 'decline', 0, '[]', 'r', '[]', 'm')")
    conn.execute(sql)
    with pytest.raises(IntegrityError):
        conn.execute(sql)
