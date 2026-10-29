import json
import sqlite3

from app.reference import Reference


def seed_claims(conn: sqlite3.Connection, ref: Reference) -> int:
    for claim in ref.claims:
        conn.execute(
            "INSERT OR IGNORE INTO claims (claim_id, status, payload) VALUES (?, 'waiting', ?)",
            (claim["claim_id"], json.dumps(claim)),
        )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
