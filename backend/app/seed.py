import json

from sqlalchemy import Engine, func, select

from app.db import claims, insert_or_ignore
from app.reference import Reference


def seed_claims(engine: Engine, ref: Reference) -> int:
    with engine.begin() as conn:
        conn.execute(
            insert_or_ignore(conn, claims),
            [{"claim_id": c["claim_id"], "status": "waiting", "payload": json.dumps(c)} for c in ref.claims],
        )
        return conn.execute(select(func.count()).select_from(claims)).scalar_one()
