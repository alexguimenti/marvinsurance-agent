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
