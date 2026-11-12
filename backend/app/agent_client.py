"""The backend's calls to the agent's service. `http` is the httpx module, or a test's stand-in for it."""
import logging

import httpx

log = logging.getLogger(__name__)


class AgentDown(Exception):
    """The agent's service could not be reached."""


class AgentRefused(Exception):
    """The agent's service said no, and why."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def _headers(key: str | None) -> dict:
    return {"X-API-Key": key} if key else {}


def start(http, url: str, path: str, body: dict, key: str | None) -> dict:
    """Ask the service to start a dispatch or an evaluation. It answers at once, or refuses while one runs."""
    try:
        answer = http.post(f"{url}{path}", json=body, timeout=10, headers=_headers(key))
    except httpx.HTTPError as exc:
        raise AgentDown(str(exc)) from exc
    if answer.status_code >= 400:
        try:
            detail = answer.json().get("detail", "the agent refused the run")
        except ValueError:  # a crash answers in plain text
            detail = answer.text or "the agent refused the run"
        raise AgentRefused(answer.status_code, str(detail))
    return answer.json()


def resume_claim(http, url: str, claim_id: str, key: str | None) -> dict:
    """Ask the service to resume a claim paused for a sign-off. It answers at once. A refusal or an unreachable service is
    raised as `start` raises it: the caller logs it and goes on."""
    return start(http, url, f"/claims/{claim_id}/resume", {}, key)
