"""The agent's entry point as a web service, separate from the app: `uvicorn agent.service:app --port 8002`.
The backend calls it, with the API key, to start a dispatch, an evaluation or a round. It answers at once and works in
the background, and keeps every record by calling the backend."""
import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import Callable, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

import httpx

from agent import api, improver
from agent.connection import Refused, call
from agent import improver_api as rounds
from agent.dispatcher import dispatch
from agent.worker import AgentEnv, open_env
from app.config import Settings, get_settings
from app.auth import key_matches

log = logging.getLogger(__name__)
BACKEND_WAIT_S = 60  # `npm run dev` starts the backend and the service at once


MAX_WORKERS = 25


class DispatchBody(BaseModel):
    workers: int = Field(ge=1, le=MAX_WORKERS)  # claim agents at once


class RoundStart(BaseModel):
    handler_id: str  # who started the round


class RoundDecision(BaseModel):
    proposal_id: int
    approve: bool
    reason: str  # a rejection's reason goes back to the improver
    handler_id: str


class EvaluationBody(BaseModel):
    eval_set: Literal["evaluation", "held_out"]
    tiny: bool  # only the set's five tiny claims
    workers: int = Field(ge=1, le=MAX_WORKERS)


def wait_for_backend(seconds: float = BACKEND_WAIT_S) -> None:
    """Wait for the backend's health check: it seeds the database, and the service records everything through it."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            call("GET", "/api/health")
            return
        except (httpx.TransportError, Refused) as exc:
            if time.monotonic() > deadline:
                raise RuntimeError(f"the backend did not answer in {seconds:.0f} seconds: {exc}") from exc
            time.sleep(0.5)


def run_label(env: AgentEnv) -> tuple[int, str, str]:
    """The configuration version a new run uses, and the model and reasoning effort it runs with."""
    config = api.configuration(None)
    model, effort = env.effective(config)
    return config["version"], model, effort


def create_service(settings: Settings, inline: bool = False) -> FastAPI:
    """With inline, work happens before a call returns, which is what tests want."""
    holder: dict[str, AgentEnv] = {}
    busy = threading.Lock()  # one dispatch, evaluation or stretch of a round at a time in this process
    state: dict[str, str | None] = {"what": None}  # what holds the lock, so a refusal can say

    def in_background(name: str, work: Callable, *args) -> None:
        def guarded() -> None:
            try:
                work(*args)
            except Exception:  # the claim stays as it is, and the next run picks it up
                log.exception("%s failed", name)

        if inline:
            guarded()
        else:
            # daemon: stopping the service stops the work, and the next run carries on from the last step
            threading.Thread(target=guarded, name=name, daemon=True).start()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if not settings.api_key:
            raise RuntimeError("UNDERCOVER_API_KEY is not set: the agent's service cannot start without the key")
        wait_for_backend()
        with open_env(settings) as env:
            holder["env"] = env
            for run_id in api.stop_running():  # whatever said running lost its process: this one
                log.warning("run %s was stopped when the service stopped; the next dispatch carries on its claims", run_id)
            for round_id in rounds.stop_working_rounds():  # a round waiting for a handler is still waiting
                log.warning("round %s was stopped when the service stopped", round_id)
            yield

    app = FastAPI(title="Marvin Agent service", lifespan=lifespan)
    app.state.settings = settings  # a test starts the backend this service records through from it

    def needs_key(request: Request) -> None:
        if not key_matches(request, settings.api_key):
            raise HTTPException(401, "this needs the agent's API key")

    guarded = APIRouter(dependencies=[Depends(needs_key)])  # only the backend calls the service, with the key

    def refusal() -> HTTPException:
        return HTTPException(409, f"{state['what'] or 'a dispatch or evaluation'} is already running")

    def start(what: Callable[[int], str], create: Callable[[AgentEnv], int], work: Callable[[AgentEnv, int], None],
              key: str = "run_id") -> dict:
        """Answer at once: refuse while something is going, or create the run (or round) and work it in the background."""
        env = holder["env"]
        if not busy.acquire(blocking=False):
            raise refusal()
        try:  # whatever goes wrong before the work starts, the lock goes back, and the caller hears of it
            if api.running_run() is not None:
                raise HTTPException(409, "a dispatch or evaluation is already running")
            run_id = create(env)
        except api.RunActive as exc:
            busy.release()
            raise HTTPException(409, str(exc))
        except BaseException:
            busy.release()
            raise

        state["what"] = what(run_id)

        def go() -> None:
            try:
                work(env, run_id)
            finally:
                state["what"] = None
                busy.release()

        in_background(state["what"], go)
        return {"status": "started", key: run_id}

    @app.get("/health", operation_id="get_agent_health")
    def health():
        """Say that the agent is up."""
        return {"status": "ok"}

    @guarded.post("/dispatches", status_code=202, operation_id="start_dispatch")
    def start_dispatch(body: DispatchBody):
        """Start a dispatch of every claim in progress and every waiting claim, with N claim agents at once."""
        def create(env: AgentEnv) -> int:
            version, model, effort = run_label(env)
            return api.start_dispatch(body.workers, model, effort, version)

        return start(lambda _: "a dispatch", create, lambda env, run_id: dispatch(env, run_id, body.workers))

    @guarded.post("/evaluations", status_code=202, operation_id="start_evaluation")
    def start_evaluation(body: EvaluationBody):
        """Start an evaluation on copies of a labelled set, whole or its tiny claims, with N claim agents at once."""
        def create(env: AgentEnv) -> int:
            version, model, effort = run_label(env)
            return api.start_evaluation(body.eval_set, body.tiny, body.workers, model, effort, version)

        return start(lambda _: "an evaluation", create, lambda env, run_id: dispatch(env, run_id, body.workers))

    @guarded.post("/rounds", status_code=202, operation_id="start_improver_round")
    def start_improver_round(body: RoundStart):
        """Start an improvement round for a handler: the evaluations the current version needs, then the improver."""
        def create(env: AgentEnv) -> int:
            try:
                return improver.begin(env, body.handler_id)
            except rounds.RoundOpen as exc:
                raise HTTPException(409, str(exc))

        return start(lambda round_id: f"round {round_id}", create, improver.work_round, key="round_id")

    @guarded.post("/rounds/{round_id}/decision", status_code=202, operation_id="decide_round_proposal")
    def decide_round_proposal(round_id: int, body: RoundDecision):
        """Record a handler's approval or rejection of the proposal a round waits on, then carry the improver on."""
        env = holder["env"]
        if not busy.acquire(blocking=False):
            raise refusal()
        try:  # the decision and who made it are recorded before the improver resumes
            improver.record_decision(env, round_id, body.proposal_id, body.approve, body.reason, body.handler_id)
        except LookupError as exc:
            busy.release()
            raise HTTPException(404, str(exc))
        except (rounds.AlreadyDecided, rounds.RoundStopped) as exc:
            busy.release()
            raise HTTPException(409, str(exc))
        except BaseException:
            busy.release()
            raise
        state["what"] = f"round {round_id}"

        def go() -> None:
            try:
                improver.resume_round(env, round_id, body.approve, body.reason)
            finally:
                state["what"] = None
                busy.release()

        in_background(f"decision on round {round_id}", go)
        return {"status": "decided", "round_id": round_id}

    app.include_router(guarded)
    return app


def __getattr__(name: str):
    # `uvicorn agent.service:app` asks for this; importing the module for its functions builds nothing.
    if name == "app":
        return create_service(get_settings())
    raise AttributeError(name)
