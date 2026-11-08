"""The scoreboard: each evaluation scored from our own records against the handlers' decisions, with the measures
from the team's labs. The only code that reads the answer key."""
from datetime import datetime, timezone

from sqlalchemy import Engine, select

from app import runs
from app.db import answer_key, claims, decisions, judge_grades, plain, run_claims
from app.db import runs as runs_table
from app.usage import per_claim

WRONG_DECLINE_COST = 1500  # the agent declined what the handlers did not
NEEDLESS_REFER_COST = 50  # the agent referred what the handlers did not
PAYOUT_TOLERANCE = 1.0  # a payout within a dollar of the handlers' counts as right


def score_claim(agent: dict | None, handler: dict) -> dict:
    """One claim against the handler's decision. A claim the agent didn't decide counts as a referral."""
    decision = agent["decision"] if agent else "refer"
    payout = float(agent["payout"]) if agent else 0.0
    expected, expected_payout = handler["decision"], float(handler["payout"])
    decision_ok = decision == expected
    off = abs(payout - expected_payout)
    if decision == "decline" and expected != "decline":
        cost = WRONG_DECLINE_COST
    elif decision == "approve" and expected != "approve":
        cost = payout  # whether or not it was paid
    elif decision == "refer" and expected != "refer":
        cost = NEEDLESS_REFER_COST
    elif decision == "approve" and off > PAYOUT_TOLERANCE:
        cost = off  # the right decision, the wrong amount
    else:
        cost = 0.0
    return {"decision_ok": decision_ok, "accurate": decision_ok and off <= PAYOUT_TOLERANCE,
            "mistake_cost": round(cost, 2), "referred": decision == "refer"}


def measures(rows: list[dict], total_seconds: float) -> dict:
    """The run's measures from its scored claims, each with its cost_usd and seconds."""
    n = len(rows)
    share = (lambda key: sum(1 for r in rows if r[key]) / n) if n else (lambda key: None)
    costs = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
    return {
        "claims": n,
        "decision_accuracy": share("decision_ok"),
        "accuracy": share("accurate"),
        "mistake_cost": round(sum(r["mistake_cost"] for r in rows), 2),
        "straight_through": (sum(1 for r in rows if not r["referred"]) / n) if n else None,
        "cost_per_claim": round(sum(costs) / n, 6) if costs and n else None,
        "seconds_per_claim": round(sum(r["seconds"] for r in rows) / n, 1) if n else None,
        "total_seconds": round(total_seconds, 1),
        # the average grade, 0 (F) to 3 (A), over the claims that were graded: an ungraded claim is not a zero
        "grade": round(sum(graded) / len(graded), 3) if (graded := [r["score"] for r in rows if r.get("score") is not None]) else None,
        "judge_cost_usd": round(sum(r.get("judge_cost_usd") or 0.0 for r in rows), 6),
    }


def _run_seconds(run) -> float:
    start, end = run["started_at"], run["finished_at"] or datetime.now(timezone.utc)
    start, end = (t if t.tzinfo else t.replace(tzinfo=timezone.utc) for t in (start, end))
    return (end - start).total_seconds()


def evaluation_report(engine: Engine, run_id: int) -> dict | None:
    """An evaluation with its measures and its claims, the agent's answer beside the handler's. None if no such run."""
    run = runs.get_run(engine, run_id)
    if run is None or run["kind"] != "evaluation":
        return None
    query = (
        select(run_claims.c.claim_id, claims.c.copy_of, claims.c.status,
               decisions.c.decision, decisions.c.payout, decisions.c.rationale,
               answer_key.c.decision.label("handler_decision"), answer_key.c.payout.label("handler_payout"),
               judge_grades.c.score, judge_grades.c.letter, judge_grades.c.same_grounds, judge_grades.c.same_section,
               judge_grades.c.cost_usd.label("judge_cost_usd"))
        .select_from(run_claims.join(claims, run_claims.c.claim_id == claims.c.claim_id)
                     .outerjoin(decisions, run_claims.c.claim_id == decisions.c.claim_id)
                     .outerjoin(judge_grades, (judge_grades.c.run_id == run_claims.c.run_id) & (judge_grades.c.claim_id == run_claims.c.claim_id))
                     .join(answer_key, (answer_key.c.claim_id == claims.c.copy_of) & (answer_key.c.eval_set == run["eval_set"])))
        .where(run_claims.c.run_id == run_id)
        .order_by(run_claims.c.position)
    )
    with engine.connect() as conn:
        found = conn.execute(query).mappings().all()
    totals = per_claim(engine, run_id)
    rows = []
    for r in found:
        agent = {"decision": r["decision"], "payout": r["payout"], "rationale": r["rationale"]} if r["decision"] else None
        handler = {"decision": r["handler_decision"], "payout": r["handler_payout"]}
        spent = totals.get(r["claim_id"], {"cost_usd": None, "seconds": 0.0})
        rows.append({"claim_id": r["claim_id"], "copy_of": r["copy_of"], "status": r["status"], "agent": agent,
                     "handler": handler, **score_claim(agent, handler),
                     "cost_usd": spent["cost_usd"], "seconds": spent["seconds"],
                     # the judge's grade; a claim it could not grade has none, and is left out of the average
                     "score": r["score"], "letter": r["letter"], "same_grounds": r["same_grounds"],
                     "same_section": r["same_section"], "judge_cost_usd": r["judge_cost_usd"]})
    return {"run": plain(run), "measures": measures(rows, _run_seconds(run)), "claims": rows}


def list_evaluations(engine: Engine) -> list[dict]:
    """Every evaluation, newest first, with its measures."""
    with engine.connect() as conn:
        ids = conn.execute(select(runs_table.c.run_id).where(runs_table.c.kind == "evaluation")
                           .order_by(runs_table.c.run_id.desc())).scalars().all()
    reports = [evaluation_report(engine, run_id) for run_id in ids]
    return [{"run": r["run"], "measures": r["measures"]} for r in reports]



def evaluation_set_review(engine: Engine, run_id: int) -> list[dict]:
    """The claims of an evaluation-set run, each with the handlers' decision, payout, reason and reference beside the
    agent's and the judge's, for the improver. The held-out set's reasons never leave: a run on it is refused."""
    report = evaluation_report(engine, run_id)
    if report is None or report["run"]["eval_set"] != "evaluation":
        raise ValueError("only the evaluation set's claims may be reviewed")
    with engine.connect() as conn:
        reasons = {r["claim_id"]: r for r in conn.execute(
            select(answer_key.c.claim_id, answer_key.c.reason, answer_key.c.reference).where(answer_key.c.eval_set == "evaluation")).mappings()}
    out = []
    for c in report["claims"]:
        original = c["copy_of"]
        out.append({
            "claim_id": original,
            "accurate": c["accurate"],
            "agent": c["agent"] or {"decision": None, "payout": None, "rationale": None},
            "handler": {**c["handler"], "reason": reasons[original]["reason"], "reference": reasons[original]["reference"]},
            "judge": {"score": c["score"], "letter": c["letter"],
                      "same_grounds": None if c["same_grounds"] is None else round(c["same_grounds"], 2),
                      "same_section": None if c["same_section"] is None else round(c["same_section"], 2)},
        })
    return out
