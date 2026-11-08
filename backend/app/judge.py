"""The judge: Jev, `typesafe/jev-1.13`, called through OpenRouter's Decisions endpoint, grades how well the claim agent
explained each decision against the handler's reason, as in the lab. It runs here in the backend, which holds the
handlers' decisions: the held-out set's reasons never leave it. The endpoint is in alpha, so the call is written once,
in `judge`."""
import json
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, as_completed

from sqlalchemy import Engine, delete, insert, select

from app.config import Settings
from app.db import answer_key, claims, decisions, judge_grades, run_claims, runs

JEV = "typesafe/jev-1.13"
CALL_TIMEOUT_MS = 10_000  # each call; the SDK's retries are off, so one slow claim cannot hold a run for long
FINISH_DEADLINE_S = 15  # all of an evaluation's calls together: finishing stays under 20 seconds

# Jev numbers the grades by their position in the list, so F is 0 and A is 3
RUBRIC = {
    "F": "different grounds from the handler's reason, or it contradicts the handler's reason",
    "C": "partly the same as the handler's reason: one of the handler's grounds or the section is missing, or it adds a ground the handler did not give",
    "B": "the same grounds and the same section of the policy wording as the handler's reason",
    "A": "the same grounds and section as the handler's reason, and clearly better expressed: clearer for the customer, with the section and the figures, shorter, and no jargon",
}
GRADES = list(RUBRIC)

JUDGE_QUESTIONS = {
    "grade": {
        "type": "score",
        "instructions": "An expert claims handler decided a travel insurance claim and gave `handler.reason`, resting on `handler.reference`. Grade how well `agent.rationale` explains the same decision.",
        "criteria": [f"{letter}: {text}" for letter, text in RUBRIC.items()],
    },
    "same_grounds": {"type": "noul", "instructions": "`agent.rationale` gives the same main grounds for the decision as `handler.reason`"},
    "same_section": {"type": "noul", "instructions": "`agent.rationale` refers to the same section of the policy wording as `handler.reference`"},
}


def make_client(settings: Settings):
    """The OpenRouter client. Imported here so nothing loads it unless a judge call is going to be made."""
    from openrouter import OpenRouter

    return OpenRouter(api_key=settings.openrouter_api_key, timeout_ms=CALL_TIMEOUT_MS)


def judge(handler: dict, agent: dict, settings: Settings, client) -> dict:
    """Ask Jev to grade one explanation. The score is Jev's `score` (0 for F to 3 for A, weighted by its probabilities);
    the letter is the level with the highest probability, never the score rounded."""
    response = client.alpha.decisions.create(
        model=JEV,
        state={"handler": handler, "agent": agent},
        questions=JUDGE_QUESTIONS,
        server_url=settings.judge_url,  # the client ignores an address set on it, so it goes on each call
        timeout_ms=CALL_TIMEOUT_MS,
        retries=None,
    )
    answers = response.answers
    probabilities = answers["grade"].probabilities
    most_likely = max(probabilities, key=probabilities.get)
    return {
        "score": answers["grade"].score,
        "letter": GRADES[int(most_likely)],
        "same_grounds": answers["same_grounds"].noul,
        "same_section": answers["same_section"].noul,
        "cost_usd": response.usage.cost,
    }


def _to_grade(engine: Engine, run_id: int) -> list[dict]:
    """Each claim of the run with the agent's decision beside the handler's reason and reference."""
    query = (
        select(run_claims.c.claim_id, decisions.c.decision, decisions.c.rationale, decisions.c.citations,
               answer_key.c.decision.label("handler_decision"), answer_key.c.reason, answer_key.c.reference)
        .select_from(run_claims.join(claims, run_claims.c.claim_id == claims.c.claim_id)
                     .outerjoin(decisions, run_claims.c.claim_id == decisions.c.claim_id)
                     .join(runs, runs.c.run_id == run_claims.c.run_id)
                     .join(answer_key, (answer_key.c.claim_id == claims.c.copy_of) & (answer_key.c.eval_set == runs.c.eval_set)))
        .where(run_claims.c.run_id == run_id).order_by(run_claims.c.position)
    )
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(query).mappings()]


def _ungraded(claim_id: str, why: str) -> dict:
    return {"claim_id": claim_id, "score": None, "letter": None, "same_grounds": None, "same_section": None, "cost_usd": 0.0, "error": why}


def grade_evaluation(engine: Engine, run_id: int, settings: Settings, client=None) -> None:
    """Grade every claim of an evaluation, in parallel, and keep the grades. A claim the agent did not decide gets an F
    without asking the judge. A claim the judge could not grade is kept as not graded, and left out of the average.
    A dispatch has nothing to grade. Running it again replaces the grades."""
    with engine.connect() as conn:
        kind = conn.execute(select(runs.c.kind).where(runs.c.run_id == run_id)).scalar_one_or_none()
    if kind != "evaluation":
        return
    rows = _to_grade(engine, run_id)
    results: dict[str, dict] = {}
    why_not = None
    if os.environ.get("MODEL_NAME"):
        why_not = "the course's free models are not graded"
    elif not settings.openrouter_api_key:
        why_not = "there is no OpenRouter key for the judge"

    to_ask = []
    for row in rows:
        if row["decision"] is None:  # no decision at all is an F
            results[row["claim_id"]] = {"claim_id": row["claim_id"], "score": 0.0, "letter": "F", "same_grounds": 0.0,
                                        "same_section": 0.0, "cost_usd": 0.0, "error": None}
        elif why_not:
            results[row["claim_id"]] = _ungraded(row["claim_id"], why_not)
        else:
            to_ask.append(row)

    if to_ask:
        client = client or make_client(settings)
        pool = ThreadPoolExecutor(max_workers=min(25, len(to_ask)))
        futures = {}
        for row in to_ask:
            citations = json.loads(row["citations"] or "[]")
            handler = {"decision": row["handler_decision"], "reason": row["reason"], "reference": row["reference"]}
            agent = {"decision": row["decision"], "rationale": row["rationale"], "section": citations[0] if citations else ""}
            futures[pool.submit(judge, handler, agent, settings, client)] = row["claim_id"]
        try:
            for future in as_completed(futures, timeout=FINISH_DEADLINE_S):
                claim_id = futures[future]
                try:
                    results[claim_id] = {"claim_id": claim_id, "error": None, **future.result()}
                except Exception as exc:  # a timeout, a refusal, an answer that could not be read
                    results[claim_id] = _ungraded(claim_id, f"the judge could not grade it: {type(exc).__name__}: {exc}")
        except FutureTimeout:
            pass
        pool.shutdown(wait=False, cancel_futures=True)  # whatever is still out has had its time
        for claim_id in futures.values():
            results.setdefault(claim_id, _ungraded(claim_id, "the judge took too long"))

    with engine.begin() as conn:
        conn.execute(delete(judge_grades).where(judge_grades.c.run_id == run_id))
        if results:
            conn.execute(insert(judge_grades), [{"run_id": run_id, **r} for r in results.values()])
