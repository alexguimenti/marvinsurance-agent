from app.db import connect, init_schema


def test_connect_uses_wal(db_path):
    conn = connect(db_path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_init_schema_is_idempotent(db_path):
    conn = connect(db_path)
    init_schema(conn)
    init_schema(conn)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"claims", "decisions", "trace_events", "payments"} <= names


def test_reset_analyses_puts_every_claim_back_in_the_queue(db_path, data_dir):
    import json

    from app.agent import ScriptedAgent
    from app.db import reset_analyses
    from app.reference import Reference
    from app.runner import run_claim
    from app.seed import seed_claims

    conn = connect(db_path)
    init_schema(conn)
    ref = Reference(data_dir)
    seed_claims(conn, ref)
    agent = ScriptedAgent(conn)
    for claim_id in ("CLM-1001", "CLM-1003"):  # CLM-1003 is over the limit, so it leaves a held payment
        run_claim(conn, ref, agent, claim_id)
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 2

    reset_analyses(conn)

    for table in ("decisions", "trace_events", "payments"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    statuses = {r["status"] for r in conn.execute("SELECT status FROM claims")}
    assert statuses == {"waiting"}
    assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 25  # the queue itself is kept
    assert json.loads(conn.execute("SELECT payload FROM claims WHERE claim_id='CLM-1001'").fetchone()[0])["claim_id"] == "CLM-1001"


def test_reset_analyses_on_an_empty_database_is_harmless(db_path):
    from app.db import reset_analyses

    conn = connect(db_path)
    init_schema(conn)
    reset_analyses(conn)
    assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0


PILOT_SCHEMA = """
CREATE TABLE claims (claim_id TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE decisions (
    claim_id TEXT PRIMARY KEY, decision TEXT NOT NULL, payout REAL NOT NULL, calculation TEXT NOT NULL,
    rationale TEXT NOT NULL, citations TEXT NOT NULL, model TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE trace_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL,
    content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE payments (
    payment_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL UNIQUE, amount REAL NOT NULL, status TEXT NOT NULL,
    approved_by TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

NEW_COLUMNS = {
    "claims": {"attempts", "last_error", "run_id"},
    "trace_events": {"run_id"},
    "decisions": {"manual_version", "wording_version"},
}


def columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_init_creates_harness_tables(db_path):
    conn = connect(db_path)
    init_schema(conn)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runs", "todo_items", "run_events"} <= names
    for table, cols in NEW_COLUMNS.items():
        assert cols <= columns(conn, table), table


def test_pilot_database_upgrades_in_place(db_path):
    conn = connect(db_path)
    conn.executescript(PILOT_SCHEMA)
    conn.execute("INSERT INTO claims VALUES ('CLM-1001', 'decided', '{}')")
    conn.execute("INSERT INTO decisions (claim_id, decision, payout, calculation, rationale, citations, model) "
                 "VALUES ('CLM-1001', 'decline', 0, '[]', 'r', '[]', 'm')")
    conn.execute("INSERT INTO trace_events (claim_id, seq, kind, content) VALUES ('CLM-1001', 1, 'status', '{}')")
    conn.commit()

    init_schema(conn)

    for table, cols in NEW_COLUMNS.items():
        assert cols <= columns(conn, table), table
    row = conn.execute("SELECT status, attempts FROM claims").fetchone()
    assert (row["status"], row["attempts"]) == ("decided", 0)
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM trace_events").fetchone()[0] == 1


def test_init_schema_twice_is_harmless(db_path):
    conn = connect(db_path)
    init_schema(conn)
    init_schema(conn)
    assert NEW_COLUMNS["claims"] <= columns(conn, "claims")


def test_reset_clears_runs_and_attempts(db_path):
    from app.db import reset_analyses

    conn = connect(db_path)
    init_schema(conn)
    conn.execute("INSERT INTO claims (claim_id, status, payload, attempts, last_error, run_id) "
                 "VALUES ('CLM-1001', 'needs_handler', '{}', 2, 'boom', 1)")
    conn.execute("INSERT INTO runs (status, model) VALUES ('finished', 'm')")
    conn.execute("INSERT INTO todo_items (run_id, position, title, status) VALUES (1, 1, 't', 'done')")
    conn.execute("INSERT INTO run_events (run_id, seq, kind, content) VALUES (1, 1, 'k', '{}')")
    conn.commit()

    reset_analyses(conn)

    for table in ("runs", "todo_items", "run_events"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    row = conn.execute("SELECT status, attempts, last_error, run_id FROM claims").fetchone()
    assert tuple(row) == ("waiting", 0, None, None)


def test_decision_cannot_be_written_twice(db_path):
    import sqlite3

    import pytest

    conn = connect(db_path)
    init_schema(conn)
    sql = ("INSERT INTO decisions (claim_id, decision, payout, calculation, rationale, citations, model) "
           "VALUES ('CLM-1001', 'decline', 0, '[]', 'r', '[]', 'm')")
    conn.execute(sql)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)
