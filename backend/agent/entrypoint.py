"""The agent as an AgentCore app: the same app on a laptop (`npm run dev` serves it with uvicorn on 127.0.0.1:8080) and on
AgentCore Runtime (`runtime/main.py` runs it). Each request is a payload that names an operation (agent/operations_in.py). An
operation checks what it must, answers at once, and does its work on a thread of its own, registered with the app as an async
task so that its `/ping` says HealthyBusy while any work is going, and a Runtime session lasts as long as its work.

Nothing happens when this module is imported or when the app starts. The agent gets ready on its first request, once in its
process, before it answers: on Runtime every session starts from one snapshot, so a key fetched or a connection opened earlier
would be shared by every session."""
import logging
import os
import random
import re
import threading
import time
from contextlib import ExitStack
from typing import Callable

import httpx
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from starlette.exceptions import HTTPException

from agent import api, improver
from agent import improver_api as rounds
from agent.connection import Refused, call
from agent.dispatcher import dispatch
from agent.operations_in import BadPayload, DecideRoundProposal, Health, ResumeClaim, StartDispatch, StartEvaluation, StartImproverRound, parse
from agent.settings import AgentSettings, key_matches
from agent.worker import AgentEnv, open_env, resume, resume_refusal

log = logging.getLogger(__name__)
BACKEND_WAIT_S = 60  # `npm run dev` starts the backend and the agent at once
_CREDENTIAL = re.compile(r"^AGENTCORE_CREDENTIAL_(.+)_NAME$")


def on_runtime() -> bool:
    """On Runtime the deployment gives the agent a setting for each credential it fetches from AgentCore Identity."""
    return any(_CREDENTIAL.match(name) for name in os.environ)


def fetch_credentials() -> None:
    """Put each credential from AgentCore Identity into the setting of its name, where `.env` puts it on a laptop. It reads the
    token Runtime sends with each request, so it runs while a request is handled, never on a thread of its own."""
    from bedrock_agentcore.identity.auth import requires_api_key

    for name in list(os.environ):
        found = _CREDENTIAL.match(name)
        if not found:
            continue

        @requires_api_key(provider_name=os.environ[name])  # makes its client when applied: so apply it here
        def fetch(*, api_key: str) -> str:
            return api_key

        os.environ[found[1]] = fetch()


def wait_for_backend(seconds: float = BACKEND_WAIT_S) -> None:
    """Wait for the backend's health check: it seeds the database, and the agent records everything through it."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            call("GET", "/api/health")
            return
        except (httpx.TransportError, Refused) as exc:
            if time.monotonic() > deadline:
                raise RuntimeError(f"the backend did not answer in {seconds:.0f} seconds: {exc}") from exc
            time.sleep(0.5)


def build_app(make_settings: Callable[[], AgentSettings] = AgentSettings, inline: bool = False) -> BedrockAgentCoreApp:
    """With inline, work happens before a call returns, which is what tests want."""
    app = BedrockAgentCoreApp()
    holder: dict = {}
    stack = ExitStack()
    ready = threading.Lock()
    busy = threading.Lock()  # one dispatch, evaluation or stretch of a round at a time in this process
    state: dict[str, str | None] = {"what": None}  # what holds the lock, so a refusal can say

    def get_ready() -> AgentEnv:
        """Once in this process: credentials (on Runtime), the agent's key, the backend, the checkpoint pool. A failure answers the
        request with the error, and the next request tries again."""
        with ready:
            if "env" in holder:
                return holder["env"]
            try:
                if on_runtime():
                    random.seed()  # every session starts from one snapshot, with the same random numbers
                    fetch_credentials()
                settings = make_settings()
                if not settings.api_key:
                    raise RuntimeError("UNDERCOVER_API_KEY is not set: the agent cannot start without the key")
                wait_for_backend()
                env = stack.enter_context(open_env(settings))
                for run_id in api.stop_running():  # whatever said running lost its process: this one
                    log.warning("run %s was stopped when the agent got ready; the next dispatch carries on its claims", run_id)
                for round_id in rounds.stop_working_rounds():  # a round waiting for a handler is still waiting
                    log.warning("round %s was stopped when the agent got ready", round_id)
            except BaseException:
                stack.close()
                raise
            holder["env"], holder["settings"] = env, settings
            return env

    def in_background(name: str, work: Callable, *args) -> None:
        task = app.add_async_task(name)  # registered before the answer: the ping says HealthyBusy until the work ends

        def guarded() -> None:
            try:
                work(*args)
            except Exception:  # the claim stays as it is, and the next run picks it up
                log.exception("%s failed", name)
            finally:
                app.complete_async_task(task)

        if inline:
            guarded()
        else:
            # daemon: stopping the agent stops the work, and the next run carries on from the last step
            threading.Thread(target=guarded, name=name, daemon=True).start()

    def run_label(env: AgentEnv) -> tuple[int, str, str]:
        config = api.configuration(None)
        model, effort = env.effective(config)
        return config["version"], model, effort

    def refusal() -> dict:
        return {"refused": f"{state['what'] or 'a dispatch or evaluation'} is already running"}

    def start(env: AgentEnv, what: str, create: Callable[[], int], work: Callable[[int], None]) -> dict:
        """Answer at once: refuse while something is going, or create the run (or round) and work it in the background."""
        if not busy.acquire(blocking=False):
            return refusal()
        try:  # whatever goes wrong before the work starts, the lock goes back, and the caller hears of it
            if api.running_run() is not None:
                busy.release()
                return {"refused": "a dispatch or evaluation is already running"}
            ident = create()
        except api.RunActive as exc:
            busy.release()
            return {"refused": str(exc)}
        except rounds.RoundOpen as exc:
            busy.release()
            return {"refused": str(exc)}
        except BaseException:
            busy.release()
            raise
        label = what if not what.startswith("round") else f"round {ident}"
        state["what"] = label

        def go() -> None:
            try:
                work(ident)
            finally:
                state["what"] = None
                busy.release()

        in_background(label, go)
        return {"accepted": True, ("round_id" if label.startswith("round") else "run_id"): ident}

    def start_dispatch(env: AgentEnv, body: StartDispatch) -> dict:
        def create() -> int:
            version, model, effort = run_label(env)
            return api.start_dispatch(body.workers, model, effort, version)

        return start(env, "a dispatch", create, lambda run_id: dispatch(env, run_id, body.workers))

    def start_evaluation(env: AgentEnv, body: StartEvaluation) -> dict:
        def create() -> int:
            version, model, effort = run_label(env)
            return api.start_evaluation(body.eval_set, body.tiny, body.workers, model, effort, version)

        return start(env, "an evaluation", create, lambda run_id: dispatch(env, run_id, body.workers))

    def start_round(env: AgentEnv, body: StartImproverRound) -> dict:
        return start(env, "round", lambda: improver.begin(env, body.handler_id), lambda round_id: improver.work_round(env, round_id))

    def decide(env: AgentEnv, body: DecideRoundProposal) -> dict:
        if not busy.acquire(blocking=False):
            return refusal()
        try:  # the decision and who made it are recorded before the improver resumes
            improver.record_decision(env, body.round_id, body.proposal_id, body.approve, body.reason, body.handler_id)
        except LookupError as exc:
            busy.release()
            return {"refused": str(exc)}
        except (rounds.AlreadyDecided, rounds.RoundStopped) as exc:
            busy.release()
            return {"refused": str(exc)}
        except BaseException:
            busy.release()
            raise
        state["what"] = f"round {body.round_id}"

        def go() -> None:
            try:
                improver.resume_round(env, body.round_id, body.approve, body.reason)
            finally:
                state["what"] = None
                busy.release()

        in_background(f"decision on round {body.round_id}", go)
        return {"accepted": True}

    def resume_claim(env: AgentEnv, body: ResumeClaim) -> dict:
        reason = resume_refusal(env, body.claim_id)
        if reason:
            return {"refused": reason}
        in_background(f"resume {body.claim_id}", resume, env, body.claim_id)  # never takes the one-at-a-time lock
        return {"accepted": True}

    def key_ok(context) -> bool:
        """Off Runtime the request must carry the agent's key. On Runtime AWS checked that the caller may invoke the runtime."""
        if on_runtime():
            return True
        headers = {k.lower(): v for k, v in (getattr(context, "request_headers", None) or {}).items()}
        presented = headers.get("authorization", "")
        return key_matches(presented.removeprefix("Bearer ").strip() if presented.startswith("Bearer ") else None, holder["settings"].api_key)

    @app.entrypoint
    def invoke(payload: dict, context) -> dict:
        try:
            body = parse(payload)
        except BadPayload as exc:
            raise HTTPException(400, str(exc)) from exc
        env = get_ready()  # a health check gets ready like any other request
        if not isinstance(body, Health) and not key_ok(context):
            raise HTTPException(401, "this needs the agent's API key")
        if isinstance(body, Health):
            return {"healthy": True}
        handler = {
            StartDispatch: start_dispatch, StartEvaluation: start_evaluation, StartImproverRound: start_round,
            DecideRoundProposal: decide, ResumeClaim: resume_claim,
        }[type(body)]
        return handler(env, body)

    app.state_holder = holder  # a test reads what the agent got ready with
    return app


# `uvicorn agent.entrypoint:app` and runtime/main.py ask for this: it builds nothing but the app itself.
app = build_app()
