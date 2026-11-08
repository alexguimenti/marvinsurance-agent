"""The agent's entry point as a web service, separate from the app: `uvicorn agent.service:app --port 8002`.
The backend calls it to start a dispatch, and after each sign-off. It answers at once and works in the background."""
import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from agent import improver
from agent import improver_backend as rounds
from agent.dispatcher import dispatch
from agent.worker import AgentEnv, carry_on, open_env, signed_off_claims
from app.config import Settings, get_settings
from app.db import claims, init_schema, make_engine
from app.reference import Reference
from app import configs, evaluations, runs
from app.auth import Handler
from app.seed import seed_claims

log = logging.getLogger(__name__)
SWEEP_S = 30
RESUME_TRIES, RESUME_WAIT_S = 10, 0.5


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


def prepare_database(settings: Settings) -> None:
    """Make sure the tables and the claims are there, with a connection that closes straight away."""
    engine = make_engine(settings, max_connections=1)
    try:
        init_schema(engine)
        seed_claims(engine, Reference(settings.data_dir))
    finally:
        engine.dispose()


def resume(env: AgentEnv, claim_id: str) -> None:
    """Pay a signed-off claim. A connection the pooler closed while idle fails the first try, so there is a second."""
    try:
        carry_on(env, claim_id)
    except Exception:
        log.warning("the first try at %s failed", claim_id, exc_info=True)
    # A worker still finishing the claim's pause holds it for a moment: wait for it rather than for the sweep.
    for _ in range(RESUME_TRIES):
        if claim_id not in signed_off_claims(env.engine):
            return
        if "already being worked" not in carry_on(env, claim_id):
            return
        time.sleep(RESUME_WAIT_S)


def sweep_signed_off(env: AgentEnv) -> None:
    """Pay whatever is signed off and still unpaid: the backend's call can be lost, or arrive while the claim is busy."""
    try:
        for claim_id in signed_off_claims(env.engine):
            resume(env, claim_id)
    except Exception:  # the next sweep tries again
        log.warning("the sweep for signed-off claims failed", exc_info=True)


def run_label(env: AgentEnv) -> tuple[int, str, str]:
    """The configuration version a new run uses, and the model and reasoning effort it runs with."""
    config = configs.current(env.engine, "config")
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
        prepare_database(settings)
        with open_env(settings) as env:
            holder["env"] = env
            for run_id in runs.stop_running(env.engine):  # whatever said running lost its process: this one
                log.warning("run %s was stopped when the service stopped; the next dispatch carries on its claims", run_id)
            for round_id in rounds.stop_working_rounds(env.engine):  # a round waiting for a handler is still waiting
                log.warning("round %s was stopped when the service stopped", round_id)
            # A sign-off is already a person's decision and costs no model call. Stopped work waits for a run.
            in_background("sweep at start", sweep_signed_off, env)
            stop = threading.Event()
            if not inline:
                def keep_sweeping() -> None:
                    while not stop.wait(SWEEP_S):
                        sweep_signed_off(env)

                threading.Thread(target=keep_sweeping, name="sweep", daemon=True).start()
            yield
            stop.set()

    app = FastAPI(title="Marvin Agent service", lifespan=lifespan)

    def refusal() -> HTTPException:
        return HTTPException(409, f"{state['what'] or 'a dispatch or evaluation'} is already running")

    def start(what: Callable[[int], str], create: Callable[[AgentEnv], int], work: Callable[[AgentEnv, int], None],
              key: str = "run_id") -> dict:
        """Answer at once: refuse while something is going, or create the run (or round) and work it in the background."""
        env = holder["env"]
        if not busy.acquire(blocking=False):
            raise refusal()
        try:  # whatever goes wrong before the work starts, the lock goes back, and the caller hears of it
            if runs.running_run(env.engine) is not None:
                raise HTTPException(409, "a dispatch or evaluation is already running")
            run_id = create(env)
        except runs.RunActive as exc:
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

    @app.post("/dispatches", status_code=202, operation_id="start_dispatch")
    def start_dispatch(body: DispatchBody):
        """Start a dispatch of every claim in progress and every waiting claim, with N claim agents at once."""
        def create(env: AgentEnv) -> int:
            version, model, effort = run_label(env)
            return runs.start_dispatch(env.engine, body.workers, model, effort, version)

        return start(lambda _: "a dispatch", create, lambda env, run_id: dispatch(env, run_id, body.workers))

    @app.post("/evaluations", status_code=202, operation_id="start_evaluation")
    def start_evaluation(body: EvaluationBody):
        """Start an evaluation on copies of a labelled set, whole or its tiny claims, with N claim agents at once."""
        def create(env: AgentEnv) -> int:
            version, model, effort = run_label(env)
            return evaluations.start_evaluation(env.engine, env.ref, body.eval_set, body.tiny, body.workers, model, effort, version)

        return start(lambda _: "an evaluation", create, lambda env, run_id: dispatch(env, run_id, body.workers))

    @app.post("/rounds", status_code=202, operation_id="start_improver_round")
    def start_improver_round(body: RoundStart):
        """Start an improvement round for a handler: the evaluations the current version needs, then the improver."""
        def create(env: AgentEnv) -> int:
            try:
                return improver.begin(env, body.handler_id)
            except rounds.RoundOpen as exc:
                raise HTTPException(409, str(exc))

        return start(lambda round_id: f"round {round_id}", create, improver.work_round, key="round_id")

    @app.post("/rounds/{round_id}/decision", status_code=202, operation_id="decide_round_proposal")
    def decide_round_proposal(round_id: int, body: RoundDecision):
        """Record a handler's approval or rejection of the proposal a round waits on, then carry the improver on."""
        env = holder["env"]
        if not busy.acquire(blocking=False):
            raise refusal()
        try:  # the decision and who made it are recorded before the improver resumes
            row = next((h for h in env.ref.handlers if h["handler_id"] == body.handler_id), None)
            if row is None:
                raise LookupError(f"unknown handler {body.handler_id}")
            handler = Handler(row["handler_id"], row["name"], row["role"], row["email"], bool(row["can_approve_over_limit"]))
            improver.record_decision(env, round_id, body.proposal_id, body.approve, body.reason, handler)
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

    @app.post("/claims/{claim_id}/resume", status_code=202, operation_id="resume_claim")
    def resume_claim(claim_id: str):
        """Pick a claim back up after its sign-off, and pay it."""
        with holder["env"].engine.connect() as conn:
            if conn.execute(select(claims.c.claim_id).where(claims.c.claim_id == claim_id)).first() is None:
                raise HTTPException(404, f"unknown claim {claim_id}")
        in_background(f"resume {claim_id}", resume, holder["env"], claim_id)
        return {"claim_id": claim_id}

    return app


def __getattr__(name: str):
    # `uvicorn agent.service:app` asks for this; importing the module for its functions builds nothing.
    if name == "app":
        return create_service(get_settings())
    raise AttributeError(name)
