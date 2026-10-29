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
