"""The agent's one way to the backend: an httpx client with the API key. Every request the claim agent, the dispatcher,
the improver and the agent's service make goes through `call`. The key and the backend's address are read from the
environment as each request is sent, so a process that loads `.env` after it imported this module still sends the key."""
import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator

import httpx

DEFAULT_BACKEND = "http://127.0.0.1:8000"
# A request waits as long as the backend's own wait for a database connection (600 s) allows, less a margin: up to 25 claim agents
# share the backend's 4 connections, and a claim agent waits for one rather than fail. The backend also grades an evaluation
# before it answers that the run is over.
TIMEOUT_S = 300

_lock = threading.Lock()
_client: httpx.Client | None = None
_in_use: httpx.Client | None = None  # a test's client, in place of the real one


class Refused(Exception):
    """The backend said no, and why."""

    def __init__(self, status_code: int, detail: Any, code: str | None = None):
        super().__init__(str(detail))
        self.status_code, self.detail, self.code = status_code, detail, code


def _http() -> httpx.Client:
    global _client
    if _in_use is not None:
        return _in_use
    with _lock:
        if _client is None:
            _client = httpx.Client(timeout=TIMEOUT_S)
        return _client


@contextmanager
def use(client: httpx.Client) -> Iterator[None]:
    """Send every request through `client` (a test's) while inside."""
    global _in_use
    before, _in_use = _in_use, client
    try:
        yield
    finally:
        _in_use = before


def backend_url() -> str:
    return (os.environ.get("UNDERCOVER_API") or os.environ.get("UNDERCOVER_BACKEND") or DEFAULT_BACKEND).rstrip("/")


def call(method: str, path: str, json: dict | None = None, params: dict | None = None) -> Any:
    """One request. A 404 is a LookupError; any other 4xx is Refused with the status and the backend's detail."""
    key = os.environ.get("UNDERCOVER_API_KEY")
    headers = {"X-API-Key": key} if key else {}
    answer = _http().request(method, f"{backend_url()}{path}", json=json, params=params, headers=headers)
    if answer.status_code == 404:
        raise LookupError(_detail(answer))
    if answer.status_code >= 400:
        detail = _detail(answer, raw=True)
        code = detail.get("code") if isinstance(detail, dict) else None
        raise Refused(answer.status_code, detail.get("message", detail) if isinstance(detail, dict) else detail, code)
    return answer.json() if answer.content else None


def _detail(answer: httpx.Response, raw: bool = False) -> Any:
    try:
        detail = answer.json().get("detail", answer.text)
    except ValueError:  # a crash answers in plain text
        detail = answer.text or f"the backend answered {answer.status_code}"
    return detail if raw else str(detail)
