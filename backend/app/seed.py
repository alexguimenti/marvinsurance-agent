import json

from sqlalchemy import Engine, func, select

from app.db import claims, drop_tables, init_schema, insert_or_ignore
from app.reference import Reference


def seed_claims(engine: Engine, ref: Reference) -> int:
    with engine.begin() as conn:
        conn.execute(
            insert_or_ignore(conn, claims),
            [{"claim_id": c["claim_id"], "status": "waiting", "payload": json.dumps(c)} for c in ref.claims],
        )
        return conn.execute(select(func.count()).select_from(claims)).scalar_one()


def reseed(engine: Engine, ref: Reference) -> int:
    """Start from nothing: our tables are made again, so an older database comes up to date, and only
    the claims are loaded. Every analysis is forgotten."""
    drop_tables(engine)
    init_schema(engine)
    return seed_claims(engine, ref)
