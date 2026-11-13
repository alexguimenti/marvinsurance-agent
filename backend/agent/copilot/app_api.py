"""The Copilot's calls to the app: every one carries the handler's sign-in and never the agent's key. A refusal is `Refused`, and an
unknown record is a LookupError, as everywhere in the agent (see `agent.connection`)."""
from typing import Any

from agent import connection


def get(token: str, path: str, params: dict | None = None) -> Any:
    return connection.call("GET", path, params=params, token=token)


def post(token: str, path: str, body: dict | None = None) -> Any:
    return connection.call("POST", path, json=body or {}, token=token)


def who(token: str) -> dict:
    """The handler the app says is signed in: handler_id, name, role, email and whether they may sign off over the limit."""
    return get(token, "/api/me")


def record_step(token: str, turn_id: int, kind: str, tool: str, main_input: str | None, input: dict | None) -> int:
    """Tell the app a step has begun, so the sidebar shows its card. Returns the step's id."""
    body = {"kind": kind, "tool": tool, "main_input": main_input, "input": input}
    return post(token, f"/api/copilot/turns/{turn_id}/steps", body)["step_id"]


def finish_step(token: str, step_id: int, status: str, output: str | None, sources: list | None = None, reason: str | None = None) -> None:
    post(token, f"/api/copilot/steps/{step_id}", {"status": status, "output": output, "sources": sources, "reason": reason})


def pause(token: str, turn_id: int, claim_id: str) -> dict:
    """Pause the turn for the handler's confirmation to sign off the claim. The app answers with the card it shows, read from its records."""
    return post(token, f"/api/copilot/turns/{turn_id}/pause", {"claim_id": claim_id})


def end(token: str, turn_id: int, answer: str, model_calls: int, cost_usd: float | None) -> None:
    post(token, f"/api/copilot/turns/{turn_id}/end", {"answer": answer, "model_calls": model_calls, "cost_usd": cost_usd})


def fail(token: str, turn_id: int, reason: str) -> None:
    post(token, f"/api/copilot/turns/{turn_id}/fail", {"reason": reason[:1000]})
