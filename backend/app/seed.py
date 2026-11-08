import json

from sqlalchemy import Engine, func, select

from app.db import answer_key, claims, drop_tables, eval_sets, held_out_claims, init_schema, insert_or_ignore
from app.configs import seed_versions
from app.reference import Reference
from app.settings_store import seed_settings

# The handlers' decisions on our two labelled sets. Seeding is the only code that reads data/evals/.
EVAL_FILES = {"evaluation": (None, "decisions.json"), "held_out": ("held_out_claims.json", "held_out_decisions.json")}


def seed_claims(engine: Engine, ref: Reference) -> int:
    """Load the queue's claims, and the labelled sets for evaluations, once. Returns the queue's size."""
    with engine.begin() as conn:
        conn.execute(insert_or_ignore(conn, claims).values(
            [{"claim_id": c["claim_id"], "status": "waiting", "payload": json.dumps(c), "submitted_on": c["submitted_on"]}
             for c in ref.claims]
        ))
    seed_answer_key(engine, ref)
    seed_settings(engine, ref)
    seed_versions(engine, ref)
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(claims).where(claims.c.copy_of.is_(None))).scalar_one()


def seed_answer_key(engine: Engine, ref: Reference) -> None:
    """The held-out claims, out of the queue; which claim is in which set; and the handlers' decisions, each in a
    table of its own."""
    folder = ref.data_dir / "evals"
    with engine.begin() as conn:
        for eval_set, (claims_file, decisions_file) in EVAL_FILES.items():
            decided = json.loads((folder / decisions_file).read_text(encoding="utf-8"))
            if claims_file:
                held_out = json.loads((folder / claims_file).read_text(encoding="utf-8"))
                conn.execute(insert_or_ignore(conn, held_out_claims).values(
                    [{"claim_id": c["claim_id"], "payload": json.dumps(c)} for c in held_out]
                ))
            conn.execute(insert_or_ignore(conn, eval_sets).values(
                [{"eval_set": eval_set, "claim_id": d["claim_id"], "tiny": bool(d["tiny"])} for d in decided]
            ))
            conn.execute(insert_or_ignore(conn, answer_key).values(
                [{"eval_set": eval_set, "claim_id": d["claim_id"], "decision": d["decision"],
                  "payout": float(d["payout"]), "reason": d["reason"], "reference": d["reference"]} for d in decided]
            ))


def reseed(engine: Engine, ref: Reference) -> int:
    """Start from nothing: our tables are made again, so an older database comes up to date, and only
    the claims and the labelled sets are loaded. Every analysis and every evaluation is forgotten."""
    drop_tables(engine)
    init_schema(engine)
    return seed_claims(engine, ref)
