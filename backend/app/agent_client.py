"""The backend's calls to the agent, an AgentCore app. A call is a payload that names an operation. It goes one of two ways:
over HTTP to `/invocations` at `UNDERCOVER_AGENT` (a laptop, or a host of its own), with the agent's key as a bearer token; or, when
`UNDERCOVER_AGENT_RUNTIME` is the ARN of the agent's runtime, to that runtime with boto3's `invoke_agent_runtime`, with no key, in a
session of its own for each request. There is one attempt at each call, with no retries: a retried call could start the same
work twice.

What the app learns from a failed call is what it may do next:
- AgentRefused: the agent answered and said no, with its reason. It took nothing.
- AgentNotReached: the request never got there, or the agent answered with an error. It took nothing.
- AgentMaybe: the request was sent and something went wrong after (a timeout, an answer cut off). The work may have started."""
import json
import logging
import uuid

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import ClientError, ConnectionError as BotoConnectionError

log = logging.getLogger(__name__)
HTTP_TIMEOUT_S = 10
RUNTIME_CONFIG = Config(retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=20)  # inside Lambda's 30 seconds


class AgentRefused(Exception):
    """The agent said no, and why."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class AgentNotReached(Exception):
    """The agent did not take the work: the request never got there (`status` is None), or the agent answered with an error
    (`status` is its status)."""

    def __init__(self, reason: str, status: int | None = None):
        super().__init__(reason)
        self.status = status


class AgentMaybe(Exception):
    """The request was sent and something went wrong after: the agent may have taken the work."""


def _answer(body: object) -> dict:
    if isinstance(body, dict) and "refused" in body:
        raise AgentRefused(str(body["refused"]))
    if not isinstance(body, dict):
        raise AgentMaybe(f"the agent answered something unexpected: {str(body)[:200]}")
    return body


def _over_http(settings, payload: dict, http) -> dict:
    headers = {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}
    try:
        answer = http.post(f"{settings.agent_url}/invocations", json=payload, timeout=HTTP_TIMEOUT_S, headers=headers)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:  # never got there
        raise AgentNotReached(str(exc)) from exc
    except httpx.HTTPError as exc:  # sent, then a timeout or an answer cut off
        raise AgentMaybe(str(exc)) from exc
    if answer.status_code >= 400:
        try:
            detail = answer.json().get("error") or answer.text
        except ValueError:
            detail = answer.text or f"the agent answered {answer.status_code}"
        raise AgentNotReached(str(detail), answer.status_code)
    try:
        return _answer(answer.json())
    except ValueError as exc:  # an answer that is not JSON
        raise AgentMaybe(str(exc)) from exc


def _over_runtime(settings, payload: dict, client) -> dict:
    client = client or boto3.client("bedrock-agentcore", config=RUNTIME_CONFIG)
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=settings.agent_runtime_arn, runtimeSessionId=uuid.uuid4().hex + uuid.uuid4().hex[:8],  # 40 characters
            payload=json.dumps(payload))
    except (BotoConnectionError, ClientError) as exc:  # never got there, or Runtime answered with an error
        raise AgentNotReached(str(exc)) from exc
    except Exception as exc:  # sent, then a timeout (botocore's HTTPClientError and its subclasses) or something else
        raise AgentMaybe(str(exc)) from exc
    try:
        return _answer(json.loads(response["response"].read()))
    except (AgentRefused, AgentMaybe):
        raise
    except Exception as exc:  # an answer cut off while it was read
        raise AgentMaybe(str(exc)) from exc


def send(settings, payload: dict, http=httpx, client=None) -> dict:
    """Send one payload to the agent and return its answer: `{"accepted": true}` (or `{"healthy": true}`). `http` is the httpx
    module or a test's stand-in; `client` a test's stand-in for boto3's client."""
    if settings.agent_runtime_arn:
        return _over_runtime(settings, payload, client)
    return _over_http(settings, payload, http)


def resume_claim(settings, claim_id: str, http=httpx, client=None) -> dict:
    """Ask the agent to resume a claim paused for a handler's answer. A failure is raised as `send` raises it: the caller logs it
    and goes on, since what the app recorded stands."""
    return send(settings, {"operation": "resume_claim", "claim_id": claim_id}, http, client)
