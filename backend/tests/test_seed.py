from app.db import connect, init_schema
from app.reference import Reference
from app.seed import seed_claims


def test_seed_inserts_25_waiting_claims(db_path, data_dir):
    conn = connect(db_path)
    init_schema(conn)
    ref = Reference(data_dir)
    assert seed_claims(conn, ref) == 25
    statuses = {r["status"] for r in conn.execute("SELECT status FROM claims")}
    assert statuses == {"waiting"}
    seed_claims(conn, ref)
    assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 25
