"""The claim agent's client for the backend: what its tools, its graph and its worker read and write, by the operations
of the backend's API. Nothing here touches the database."""
from datetime import datetime
from typing import Any

from agent.connection import Refused, call


def get_claim(claim_id: str) -> dict:
    """The claim as the customer wrote it."""
    return call("GET", f"/api/claims/{claim_id}")["claim"]


def get_policy(claim_id: str) -> dict:
    return call("GET", f"/api/claims/{claim_id}/policy")


def get_claim_history(claim_id: str) -> dict:
    return call("GET", f"/api/claims/{claim_id}/history")


def list_sections(document: str) -> list[dict]:
    """The numbered sections of a document, for the claim prompt's index."""
    return call("GET", f"/api/agent/documents/{document}")


def record_decision(claim_id: str, decision: dict, model: str, decided_by: str) -> dict:
    """Record a decision the agent makes itself (the security check's referral): the model's own go through its tool."""
    return call("POST", f"/api/claims/{claim_id}/decision", json={**decision, "model": model, "decided_by": decided_by})


def gateway_refused(claim_id: str) -> bool:
    """The Gateway refused the claim's payment: put it on the approvals list. True the first time."""
    return call("POST", f"/api/agent/claims/{claim_id}/gateway-refused")["first_time"]


# --- the claim's own bookkeeping ----------------------------------------------------------------

def claim_state(claim_id: str) -> dict:
    """Status, thread, attempts, run, decision, payment and whether a sign-off is needed, in one read."""
    return call("GET", f"/api/agent/claims/{claim_id}/state")


def add_step(claim_id: str, kind: str, content: dict) -> None:
    call("POST", f"/api/agent/claims/{claim_id}/steps", json={"kind": kind, "content": content})


def hold(claim_id: str) -> bool:
    """Put the payout on the approvals list. True the first time."""
    return call("POST", f"/api/agent/claims/{claim_id}/hold")["first_time"]


def take_sent_back(claim_id: str) -> bool:
    """Take a sent-back claim for work, once: True only for the first caller."""
    return call("POST", f"/api/agent/claims/{claim_id}/take-sent-back")["taken"]


def heard(claim_id: str) -> None:
    """Say that a resume is still working the claim."""
    call("POST", f"/api/agent/claims/{claim_id}/heard")


def take(claim_id: str, run_id: int | None) -> dict:
    return call("POST", f"/api/agent/claims/{claim_id}/take", json={"run_id": run_id})


def fail(claim_id: str, attempt: int, max_attempts: int, message: str) -> str:
    return call("POST", f"/api/agent/claims/{claim_id}/fail",
                json={"attempt": attempt, "max_attempts": max_attempts, "message": message})["status"]


def carry_on(claim_id: str, run_id: int | None) -> None:
    call("POST", f"/api/agent/claims/{claim_id}/carry-on", json={"run_id": run_id})


def record_model_call(claim_id: str, run_id: int | None, model: str, usage: dict[str, Any]) -> None:
    call("POST", "/api/agent/model-calls", json={"claim_id": claim_id, "run_id": run_id, "model": model, "usage": usage, "round_id": None})


def record_work_time(claim_id: str, run_id: int | None, started_at: datetime, seconds: float) -> None:
    call("POST", "/api/agent/work-times",
         json={"claim_id": claim_id, "run_id": run_id, "started_at": started_at.isoformat(), "seconds": seconds})


def configuration(run_id: int | None) -> dict:
    """The configuration version a run uses, or the current one outside a run."""
    return call("GET", f"/api/agent/runs/{run_id}/configuration" if run_id is not None else "/api/agent/configuration")


# --- what the agent's service asks the backend to keep: runs and rounds ---

class RoundOpen(Exception):
    """Another round is still open."""


class RoundStopped(Exception):
    """The round has ended, so nothing more is recorded on it."""


class AlreadyDecided(Exception):
    """A handler already approved or rejected this proposal."""


class RunActive(Exception):
    """Another dispatch or evaluation is still going."""


CODES = {"round_open": RoundOpen, "round_stopped": RoundStopped, "already_decided": AlreadyDecided, "run_active": RunActive}


def ask(method: str, path: str, **kwargs) -> Any:
    """One call to a key-only route. A 409 with a code in its detail is the exception that code names."""
    try:
        return call(method, f"/api/agent{path}", **kwargs)
    except Refused as exc:
        if exc.status_code == 409 and exc.code in CODES:
            raise CODES[exc.code](str(exc)) from exc
        raise


# --- runs ----------------------------------------------------------------------------------------

def start_evaluation(eval_set: str, tiny: bool, workers: int, model: str, effort: str | None,
                     config_version: int | None = None, round_id: int | None = None) -> int:
    return ask("POST", "/evaluations", json={
        "eval_set": eval_set, "tiny": tiny, "workers": workers, "model": model, "reasoning_effort": effort,
        "config_version": config_version, "round_id": round_id})["run_id"]


def running_run() -> dict | None:
    return ask("GET", "/runs/running")


def heard_run(run_id: int) -> None:
    """Say that the agent is still working a run."""
    ask("POST", f"/runs/{run_id}/heard")


def get_run(run_id: int) -> dict:
    return ask("GET", f"/runs/{run_id}")


def run_status(run_id: int) -> str:
    return get_run(run_id)["status"]


def run_claim_ids(run_id: int) -> list[str]:
    return ask("GET", f"/runs/{run_id}/claims")


def claims_to_dispatch() -> list[str]:
    return ask("GET", "/claims-to-dispatch")


def add_run_claims(run_id: int, claim_ids: list[str]) -> None:
    if claim_ids:
        ask("POST", f"/runs/{run_id}/claims", json={"claim_ids": claim_ids})


def claim_started(run_id: int, claim_id: str) -> None:
    ask("POST", f"/runs/{run_id}/claims/{claim_id}/started")


def claim_finished(run_id: int, claim_id: str) -> None:
    ask("POST", f"/runs/{run_id}/claims/{claim_id}/finished")


def finish_run(run_id: int, error: str | None = None) -> None:
    """End a run. With no error, the backend grades an evaluation before it answers."""
    ask("POST", f"/runs/{run_id}/finish", json={"error": error})


