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


def notify_sign_off(http, url: str, claim_id: str) -> None:
    """Tell the agent a claim is signed off. The sign-off stands either way: if the agent can't be reached,
    it picks the claim up the next time it starts or runs."""
    try:
        http.post(f"{url}/claims/{claim_id}/resume", timeout=2)
    except httpx.HTTPError as exc:
        log.warning("the agent did not hear about the sign-off of %s: %s", claim_id, exc)


def start_run(http, url: str, path: str, body: dict) -> dict:
    try:
        answer = http.post(f"{url}{path}", json=body, timeout=10)
    except httpx.HTTPError as exc:
        raise AgentDown(str(exc)) from exc
    if answer.status_code >= 400:
        raise AgentRefused(answer.status_code, answer.json().get("detail", "the agent refused the run"))
    return answer.json()
