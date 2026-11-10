"""The improver's only way to the records: the rounds and the versions, by the backend's key-only routes, over the
one connection. The claim agent's own modules (the graph, its tools, the worker,
the prompts) never import what is here: they have no way to the scores, the answer key or the handlers' decisions. A
test reads their imports to check it."""
from agent.api import AlreadyDecided, RoundOpen, RoundStopped, RunActive, ask as _ask  # noqa: F401


# --- versions and settings -----------------------------------------------------------------------

def current_version(kind: str) -> dict:
    return _ask("GET", f"/versions/{kind}/current")


def get_version(kind: str, version: int) -> dict:
    return _ask("GET", f"/versions/{kind}/{version}")


def get_settings() -> dict:
    return _ask("GET", "/platform-settings")


def models_allowed() -> dict:
    return _ask("GET", "/models-allowed")


def version_scores(version: int) -> dict:
    return _ask("GET", f"/scores/{version}")


def needs_first_evaluations(version: int) -> list[str]:
    return _ask("GET", f"/owed-evaluations/{version}")


def set_size(eval_set: str) -> int:
    return _ask("GET", f"/sets/{eval_set}/size")["size"]


def estimate(model: str, claims: int, models: dict, current_model: str) -> float:
    return _ask("POST", "/estimate", json={"model": model, "claims": claims, "models": models, "current_model": current_model})["cost"]


def keep_or_roll_back(after: dict, before: dict, settings: dict) -> tuple[bool, str]:
    answer = _ask("POST", "/keep-or-roll-back", json={"after": after, "before": before, "settings": settings})
    return answer["kept"], answer["why"]


# --- rounds --------------------------------------------------------------------------------------

def start_round(handler_id: str) -> int:
    return _ask("POST", "/rounds", json={"handler_id": handler_id})["round_id"]


def stop_working_rounds() -> list[int]:
    return _ask("POST", "/rounds/stop-working")


def get_round(round_id: int) -> dict:
    return _ask("GET", f"/rounds/{round_id}")


def round_spent(round_id: int) -> float:
    return _ask("GET", f"/rounds/{round_id}/spent")["spent"]


def improver_view(round_id: int) -> dict:
    return _ask("GET", f"/rounds/{round_id}/view")


def set_stage(round_id: int, stage: str, progress: str | None = None) -> None:
    _ask("POST", f"/rounds/{round_id}/stage", json={"stage": stage, "progress": progress})


def add_proposal(round_id: int, kind: str, payload: dict, reason: str) -> int:
    return _ask("POST", f"/rounds/{round_id}/proposals", json={"kind": kind, "payload": payload, "reason": reason})["id"]


def get_proposal(proposal_id: int) -> dict:
    return _ask("GET", f"/proposals/{proposal_id}")


def decide_proposal(proposal_id: int, approve: bool, reason: str, handler_id: str) -> dict:
    return _ask("POST", f"/proposals/{proposal_id}/decision", json={"approve": approve, "reason": reason, "handler_id": handler_id})


def record_verdict(round_id: int, kept: bool, why: str, before: dict, after: dict) -> None:
    _ask("POST", f"/rounds/{round_id}/verdict", json={"kept": kept, "why": why, "before": before, "after": after})


def finish_round(round_id: int, error: str | None = None) -> None:
    _ask("POST", f"/rounds/{round_id}/finish", json={"error": error})


def record_model_call(round_id: int, model: str, usage: dict) -> None:
    """One of the improver's own model replies, with its round instead of a claim and a run."""
    _ask("POST", "/model-calls", json={"claim_id": "", "run_id": None, "model": model, "usage": usage, "round_id": round_id})
