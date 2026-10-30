"""Score the Farting Agent against the handlers' decisions.

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


def _decisions_from(conn, claim_ids: set[str]) -> list[dict]:
    rows = conn.execute("SELECT claim_id, decision, payout FROM decisions").fetchall()
    return [dict(r) for r in rows if r["claim_id"] in claim_ids]


def _run_agent(claims: list[dict], settings) -> list[dict]:
    from app.agent import build_agent
    from app.db import connect, init_schema
    from app.reference import Reference
    from app.runner import work_claim

    with tempfile.TemporaryDirectory() as tmp:
        conn = connect(str(Path(tmp) / "score.db"))
        init_schema(conn)
        ref = Reference(settings.data_dir)
        for claim in claims:
            conn.execute("INSERT INTO claims (claim_id, status, payload) VALUES (?, 'waiting', ?)",
                         (claim["claim_id"], json.dumps(claim)))
        conn.commit()
        agent = build_agent(settings, conn, ref)
        for claim in claims:
            print(f"running {claim['claim_id']} ...", file=sys.stderr)
            for _ in range(settings.agent_max_attempts):  # a failed attempt puts the claim back in the queue
                work_claim(conn, ref, agent, claim["claim_id"], None, settings.agent_max_attempts)
        result = _decisions_from(conn, {c["claim_id"] for c in claims})
        conn.close()
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
        from app.db import connect
        conn = connect(settings.db_path)
        decisions = _decisions_from(conn, ids)
        conn.close()

    result = score(decisions, expected)
    print(f"model: {settings.agent_model}  split: {args.split}  claims: {len(expected)}")
    print(f"decision accuracy: {result['decision_accuracy']:.0%}")
    print(f"payout accuracy:   {result['payout_accuracy']:.0%}")
    for line in result["mismatches"]:
        print("  -", line)


if __name__ == "__main__":
    main()
