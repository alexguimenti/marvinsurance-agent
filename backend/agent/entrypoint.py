"""The agent as an AgentCore app: the same app on a laptop (`npm run dev` serves it with uvicorn on 127.0.0.1:8080) and on
AgentCore Runtime (`runtime/main.py` runs it). Each request is a payload that names an operation (agent/operations_in.py). An
operation checks what it must, answers at once, and does its work on a thread of its own, registered with the app as an async
task so that its `/ping` says HealthyBusy while any work is going, and a Runtime session lasts as long as its work.

The app creates the work (a run, a round, a handler's review) and asks the agent to work it by its id; the agent keeps no one-at-a-time
rule of its own, since on Runtime each session is a process of its own. While it works it tells the app every minute, and when its
work fails it stops only that run or round.

Nothing happens when this module is imported or when the app starts. The agent gets ready on its first request, once in its
process, before it answers: on Runtime every session starts from one snapshot, so a key fetched or a connection opened earlier
would be shared by every session."""
import logging
import os
import random
import re
import threading
from contextlib import ExitStack
from typing import Callable

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from starlette.exceptions import HTTPException

from agent import api, improver
from agent import improver_api as rounds
from agent.dispatcher import dispatch
from agent.heartbeat import heartbeat
from agent.operations_in import BadPayload, CarryRoundOn, Health, ResumeClaim, WorkRound, WorkRun, parse
from agent.settings import AgentSettings, key_matches
from agent.worker import AgentEnv, open_env, resume, resume_refusal

log = logging.getLogger(__name__)
# The CLI gives each credential's provider name in CREDENTIAL_<NAME>_NAME (AgentCore CLI 1.0.2) or AGENTCORE_CREDENTIAL_<NAME>_NAME (rc.5)
_CREDENTIAL = re.compile(r"^(?:AGENTCORE_)?CREDENTIAL_(.+)_NAME$")


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


def build_app(make_settings: Callable[[], AgentSettings] = AgentSettings, inline: bool = False) -> BedrockAgentCoreApp:
    """With inline, work happens before a call returns, which is what tests want."""
    app = BedrockAgentCoreApp()
    holder: dict = {}
    stack = ExitStack()
    ready = threading.Lock()

    def get_ready() -> AgentEnv:
        """Once in this process: credentials (on Runtime), the agent's key, the checkpoint pool. A failure answers the request with
        the error, and the next request tries again."""
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
                env = stack.enter_context(open_env(settings))
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

    def reason_of(exc: BaseException) -> str:
        return f"{type(exc).__name__}: {exc}"

    def work_run(env: AgentEnv, body: WorkRun) -> dict:
        try:
            run = api.get_run(body.run_id)
        except LookupError as exc:
            return {"refused": str(exc)}
        if run["status"] != "running":
            return {"refused": f"run {body.run_id} is not running"}

        def job() -> None:
            with heartbeat(lambda: api.heard_run(body.run_id)):
                try:
                    dispatch(env, body.run_id, run["workers"])
                except Exception as exc:  # it stops this run and nothing else
                    log.exception("run %s failed", body.run_id)
                    api.finish_run(body.run_id, error=reason_of(exc))

        in_background(f"run {body.run_id}", job)
        return {"accepted": True}

    def open_round(round_id: int) -> dict | None:
        try:
            found = rounds.get_round(round_id)
        except LookupError:
            return None
        return found if found["open"] else None

    def work_round(env: AgentEnv, body: WorkRound) -> dict:
        if open_round(body.round_id) is None:
            return {"refused": f"round {body.round_id} is not open"}

        def job() -> None:
            with heartbeat(lambda: rounds.heard_round(body.round_id)):
                try:
                    improver.work_round(env, body.round_id)
                except Exception as exc:  # the improver keeps its own errors; this is what it could not
                    log.exception("round %s failed", body.round_id)
                    rounds.stop_round(body.round_id, reason_of(exc))

        in_background(f"round {body.round_id}", job)
        return {"accepted": True}

    def carry_round_on(env: AgentEnv, body: CarryRoundOn) -> dict:
        if open_round(body.round_id) is None:
            return {"refused": f"round {body.round_id} is not open"}
        try:
            proposal = rounds.get_proposal(body.proposal_id)
        except LookupError as exc:
            return {"refused": str(exc)}
        if proposal["round_id"] != body.round_id or proposal["status"] not in ("approved", "rejected"):
            return {"refused": f"proposal {body.proposal_id} has no review in round {body.round_id}"}

        def job() -> None:
            with heartbeat(lambda: rounds.heard_round(body.round_id)):
                try:
                    improver.resume_round(env, body.round_id, proposal["status"] == "approved", proposal.get("decision_reason") or "")
                except Exception as exc:
                    log.exception("round %s failed", body.round_id)
                    rounds.stop_round(body.round_id, reason_of(exc))

        in_background(f"round {body.round_id}", job)
        return {"accepted": True}

    def resume_claim(env: AgentEnv, body: ResumeClaim) -> dict:
        reason = resume_refusal(env, body.claim_id)
        if reason:
            return {"refused": reason}
        in_background(f"resume {body.claim_id}", resume, env, body.claim_id)  # a resume runs beside anything
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
        handler = {WorkRun: work_run, WorkRound: work_round, CarryRoundOn: carry_round_on, ResumeClaim: resume_claim}[type(body)]
        return handler(env, body)

    app.state_holder = holder  # a test reads what the agent got ready with
    return app


# `uvicorn agent.entrypoint:app` and runtime/main.py ask for this: it builds nothing but the app itself.
app = build_app()
