"""The dispatcher, the agent's fifth part: a graph with no model that works a run's claims, up to N at once.

    list_claims --Send per claim--> work_claim (max_concurrency = N)

Code chooses the claims and keeps track of the run. Each claim is worked by the claim agent on its own thread,
through `worker.work`, the one place the dispatcher touches it. The dispatcher saves no progress of its own: each
claim saves its own, and the next dispatch finishes what a stopped one left.
"""
import logging
import operator
from typing import Annotated, TypedDict

from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy, Send
from pydantic import BaseModel, Field

from agent.worker import RETRYABLE, AgentEnv, work
from app import runs
from app.claims import get_claim

log = logging.getLogger(__name__)

# A rate limit or a timeout waits and carries on from the claim's last saved step, as the same attempt.
CLAIM_RETRY = RetryPolicy(initial_interval=5, backoff_factor=2, max_interval=60, max_attempts=4, retry_on=RETRYABLE)


class DispatchState(BaseModel):
    run_id: int
    outcomes: Annotated[list[dict], operator.add] = Field(default_factory=list)  # the reducer joins the workers' lines


class ClaimTask(TypedDict):
    """What the dispatcher sends to one claim agent: the claim and its run."""
    claim_id: str
    run_id: int


def build_dispatcher(env: AgentEnv, retry: RetryPolicy):
    engine = env.engine

    def list_claims(state: DispatchState) -> Command:
        """List the run's claims and send each to its own claim agent. An evaluation's copies were listed when it
        was created; a dispatch takes every claim in progress and every waiting one."""
        if runs.get_run(engine, state.run_id)["kind"] == "evaluation":
            claim_ids = runs.run_claim_ids(engine, state.run_id)
        else:
            claim_ids = [c for c in runs.claims_to_dispatch(engine) if not env.is_working(c)]  # a resume has those
            runs.add_run_claims(engine, state.run_id, claim_ids)
        return Command(goto=[Send("work_claim", ClaimTask(claim_id=c, run_id=state.run_id)) for c in claim_ids])

    def work_claim(task: ClaimTask) -> dict:
        """Work one claim. One that failed and went back to the queue is tried once more, straight away."""
        claim_id, run_id = task["claim_id"], task["run_id"]
        runs.claim_started(engine, run_id, claim_id)
        line = work(env, claim_id, run_id)
        if get_claim(engine, claim_id)["status"] == "waiting":
            line = work(env, claim_id, run_id)
        runs.claim_finished(engine, run_id, claim_id)
        return {"outcomes": [{"claim_id": claim_id, "line": line}]}

    def on_claim_error(task: ClaimTask, error: NodeError) -> dict:
        """Whatever went wrong, the dispatch goes on. The claim stays where it is, for the next dispatch."""
        claim_id, run_id = task["claim_id"], task["run_id"]
        message = f"{type(error.error).__name__}: {error.error}"
        log.warning("%s: %s", claim_id, message)
        try:
            runs.add_trace_event(engine, claim_id, "error", {"message": f"{message}. It stays as it is for the next dispatch."})
            runs.claim_finished(engine, run_id, claim_id)
        except Exception:  # the records can wait; the dispatch must not stop
            log.exception("could not record what happened to %s", claim_id)
        return {"outcomes": [{"claim_id": claim_id, "line": f"{claim_id}: {message}. Left for the next dispatch."}]}

    builder = StateGraph(DispatchState)
    builder.add_node("list_claims", list_claims, destinations=("work_claim",))
    builder.add_node("work_claim", work_claim, retry_policy=retry, error_handler=on_claim_error)
    builder.add_edge(START, "list_claims")
    builder.add_edge("work_claim", END)
    return builder.compile()  # no checkpointer: the claims save their own progress


def dispatch(env: AgentEnv, run_id: int, workers: int, retry: RetryPolicy = CLAIM_RETRY) -> None:
    """Work the run's claims with up to `workers` claim agents at once, then close the run."""
    try:
        build_dispatcher(env, retry).invoke(DispatchState(run_id=run_id), {"max_concurrency": workers})
    except Exception as exc:  # the dispatcher itself broke, not a claim: the run ends stopped, and says why
        runs.finish_run(env.engine, run_id, error=f"{type(exc).__name__}: {exc}")
        raise
    runs.finish_run(env.engine, run_id)
