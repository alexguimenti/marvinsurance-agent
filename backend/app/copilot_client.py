"""The backend's calls to the Copilot, an AgentCore app of its own. A call is a payload that names an operation (a turn, or the
handler's answer to a confirmation). It goes one of two ways: over HTTP to `/invocations` at `UNDERCOVER_COPILOT` (a laptop), with no
key, since the payload carries the handler's sign-in; or, when `UNDERCOVER_COPILOT_RUNTIME` is the ARN of the Copilot's runtime, to
that runtime with boto3's `invoke_agent_runtime`, with the conversation's id as the Runtime session id.

What the app learns from a failed call is what it may do next, as for the claim agent (see `agent_client`): a refusal and a request
that never got there mean the Copilot took nothing; a timeout after the request was sent means it may have. While Runtime starts or
stops a session a call gets a conflict that is safe to retry, since it has not reached the Copilot: it is retried a few times."""
import json
import time
from typing import Callable

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import ClientError, ConnectionError as BotoConnectionError

from app.agent_client import AgentMaybe, AgentNotReached, AgentRefused, _answer  # noqa: F401

HTTP_TIMEOUT_S = 10
SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
CONFLICT_RETRIES = 4
CONFLICT_WAIT_S = 1
RUNTIME_CONFIG = Config(retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=20)  # inside Lambda's 30 seconds


def _over_http(settings, payload: dict, session_id: str, http) -> dict:
    try:
        answer = http.post(f"{settings.copilot_url}/invocations", json=payload, timeout=HTTP_TIMEOUT_S, headers={SESSION_HEADER: session_id})
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:  # never got there
        raise AgentNotReached(str(exc)) from exc
    except httpx.HTTPError as exc:  # sent, then a timeout or an answer cut off
        raise AgentMaybe(str(exc)) from exc
    if answer.status_code >= 400:
        try:
            detail = answer.json().get("error") or answer.text
        except ValueError:
            detail = answer.text or f"the Copilot answered {answer.status_code}"
        raise AgentNotReached(str(detail), answer.status_code)
    try:
        return _answer(answer.json())
    except ValueError as exc:  # an answer that is not JSON
        raise AgentMaybe(str(exc)) from exc


def _over_runtime(settings, payload: dict, session_id: str, client, sleep: Callable[[float], None]) -> dict:
    client = client or boto3.client("bedrock-agentcore", config=RUNTIME_CONFIG)
    for attempt in range(CONFLICT_RETRIES + 1):
        try:
            response = client.invoke_agent_runtime(agentRuntimeArn=settings.copilot_runtime_arn, runtimeSessionId=session_id,
                                                   payload=json.dumps(payload))
            break
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConflictException" and attempt < CONFLICT_RETRIES:
                sleep(CONFLICT_WAIT_S)  # the session is starting or stopping, and the call has not reached the Copilot
                continue
            raise AgentNotReached(str(exc)) from exc
        except BotoConnectionError as exc:  # never got there
            raise AgentNotReached(str(exc)) from exc
        except Exception as exc:  # sent, then a timeout or something else
            raise AgentMaybe(str(exc)) from exc
    try:
        return _answer(json.loads(response["response"].read()))
    except (AgentRefused, AgentMaybe):
        raise
    except Exception as exc:  # an answer cut off while it was read
        raise AgentMaybe(str(exc)) from exc


def send(settings, payload: dict, session_id: str, http=httpx, client=None, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Send one payload to the Copilot and return its answer: `{"accepted": true}` (or `{"healthy": true}`). `http` is the httpx
    module or a test's stand-in; `client` a test's stand-in for boto3's client."""
    if settings.copilot_runtime_arn:
        return _over_runtime(settings, payload, session_id, client, sleep)
    return _over_http(settings, payload, session_id, http)
