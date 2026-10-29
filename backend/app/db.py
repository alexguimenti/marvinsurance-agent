import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    claim_id TEXT PRIMARY KEY,
    decision TEXT NOT NULL,
    payout REAL NOT NULL,
    calculation TEXT NOT NULL,
    rationale TEXT NOT NULL,
    citations TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS trace_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS payments (
    payment_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL UNIQUE,
    amount REAL NOT NULL,
    status TEXT NOT NULL,
    approved_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def reset_analyses(conn: sqlite3.Connection) -> None:
    """Forget every analysis: decisions, traces and payments go, and each claim is waiting again."""
    for table in ("trace_events", "payments", "decisions"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("UPDATE claims SET status = 'waiting'")
    conn.commit()

