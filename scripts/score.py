"""Score the Marvin Agent against the handlers' decisions.

This is the ONLY code that reads data/evals/. The app never imports it.

    python scripts/score.py --split queue            # score what the app already decided
    python scripts/score.py --split queue --run      # run the agent on the queue first (temp DB)
    python scripts/score.py --split held_out --tiny  # run the agent on the 5 tiny held-out claims
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

EVALS = ROOT / "data" / "evals"
CENTS = 0.01


def isolated_settings(settings, db_path: str):
    """A scoring run never touches the real records, whatever DATABASE_URL says."""
    return settings.model_copy(update={"db_path": db_path, "database_url": None})


def select_tiny(expected: list[dict]) -> list[dict]:
    return [e for e in expected if e.get("tiny")]


def score(decisions: list[dict], expected: list[dict]) -> dict:
    by_id = {d["claim_id"]: d for d in decisions}
    decision_ok = payout_ok = 0
    mismatches: list[str] = []
    for exp in expected:
        got = by_id.get(exp["claim_id"])
        got_decision = got["decision"] if got else "none"
        got_payout = got["payout"] if got else 0.0
        right_decision = got_decision == exp["decision"]
        right_payout = got is not None and abs(got_payout - exp["payout"]) <= CENTS
        decision_ok += right_decision
        payout_ok += right_payout
        if not (right_decision and right_payout):
            mismatches.append(
                f"{exp['claim_id']}: expected {exp['decision']} ${exp['payout']:.2f}, "
                f"got {got_decision} ${got_payout:.2f} "
                f"[{exp.get('difficulty')}, needs={exp.get('needs')}]"
            )
    total = len(expected) or 1
    return {
        "decision_accuracy": decision_ok / total,
        "payout_accuracy": payout_ok / total,
        "mismatches": mismatches,
    }


def _load(name: str) -> list[dict]:
    return json.loads((EVALS / name).read_text(encoding="utf-8"))


def _decisions_from(engine, claim_ids: set[str]) -> list[dict]:
    from sqlalchemy import select

    from app.db import decisions

    with engine.connect() as conn:
        rows = conn.execute(select(decisions.c.claim_id, decisions.c.decision, decisions.c.payout)).mappings().all()
    return [dict(r) for r in rows if r["claim_id"] in claim_ids]


def _run_agent(claims: list[dict], settings) -> list[dict]:
    from sqlalchemy import insert

    from agent.worker import open_env, work_claim
    from app.db import claims as claims_table, init_schema, make_engine

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        scratch = isolated_settings(settings, str(Path(tmp) / "score.db"))
        engine = make_engine(scratch, max_connections=1)
        init_schema(engine)
        with engine.begin() as conn:
            conn.execute(insert(claims_table), [
                {"claim_id": c["claim_id"], "status": "waiting", "payload": json.dumps(c)} for c in claims
            ])
        with open_env(scratch) as env:
            for claim in claims:
                print(f"running {claim['claim_id']} ...", file=sys.stderr)
                for _ in range(settings.agent_max_attempts):  # a failed attempt puts the claim back in the queue
                    work_claim(env, claim["claim_id"], None, settings.agent_max_attempts)  # one held for sign-off counts
        result = _decisions_from(engine, {c["claim_id"] for c in claims})
        engine.dispose()
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=["queue", "held_out"], required=True)
    parser.add_argument("--tiny", action="store_true", help="only the 5 tiny claims")
    parser.add_argument("--run", action="store_true", help="run the agent now in a temp database")
    args = parser.parse_args()

    from app.config import get_settings

    settings = get_settings()
    if args.split == "queue":
        expected = _load("decisions.json")
        claims = json.loads((ROOT / "data" / "claims.json").read_text(encoding="utf-8"))
    else:
        expected = _load("held_out_decisions.json")
        claims = _load("held_out_claims.json")
        args.run = True
    if args.tiny:
        expected = select_tiny(expected)
    ids = {e["claim_id"] for e in expected}

    if args.run:
        decisions = _run_agent([c for c in claims if c["claim_id"] in ids], settings)
    else:
        from app.db import make_engine
        engine = make_engine(settings, max_connections=2)
        decisions = _decisions_from(engine, ids)
        engine.dispose()

    result = score(decisions, expected)
    print(f"model: {settings.agent_model}  split: {args.split}  claims: {len(expected)}")
    print(f"decision accuracy: {result['decision_accuracy']:.0%}")
    print(f"payout accuracy:   {result['payout_accuracy']:.0%}")
    for line in result["mismatches"]:
        print("  -", line)


if __name__ == "__main__":
    main()
