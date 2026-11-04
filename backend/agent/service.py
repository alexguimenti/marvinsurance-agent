"""The agent's entry point as a web service, separate from the app: `uvicorn agent.service:app --port 8002`.
The backend calls it to start runs, and after each sign-off."""
import logging
import threading
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from agent.queue import begin_run, work_run
from agent.worker import AgentEnv, carry_on, open_env, signed_off_claims
from app.claims import get_claim
from app.config import Settings, get_settings
from app.db import claims, init_schema, make_engine
from app.reference import Reference
from app.runs import RunActive
from app.seed import seed_claims

log = logging.getLogger(__name__)


class RunLimit(BaseModel):
    limit: int = Field(ge=1)


class SelectedClaims(BaseModel):
    claim_ids: list[str] = Field(min_length=1)


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
    carry_on(env, claim_id)
    if claim_id in signed_off_claims(env.engine):
        carry_on(env, claim_id)


def create_service(settings: Settings, inline: bool = False) -> FastAPI:
    """With inline, work happens before a call returns, which is what tests want."""
    holder: dict[str, AgentEnv] = {}

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
            # A sign-off is already a person's decision and costs no model call. Stopped work waits for a run.
            for claim_id in signed_off_claims(env.engine):
                in_background(f"resume {claim_id}", resume, env, claim_id)
            yield

    app = FastAPI(title="Farting Agent service", lifespan=lifespan)

    def start(limit: int | None, claim_ids: list[str] | None) -> dict:
        env = holder["env"]
        try:
            run_id, adopted = begin_run(env, limit, claim_ids)
        except RunActive as exc:
            raise HTTPException(409, str(exc))
        in_background(f"run {run_id}", work_run, env, run_id, adopted)
        return {"run_id": run_id}

    @app.get("/health", operation_id="get_agent_health")
    def health():
        """Say that the agent is up."""
        return {"status": "ok"}

    @app.post("/runs", status_code=202, operation_id="start_run")
    def start_run(body: RunLimit):
        """Start a run over the waiting claims, up to a limit, after carrying on unfinished work."""
        return start(body.limit, None)

    @app.post("/runs/selected", status_code=202, operation_id="start_selected_run")
    def start_selected_run(body: SelectedClaims):
        """Start a run limited to the given claims, after carrying on unfinished work."""
        for claim_id in body.claim_ids:
            _known(claim_id)
        return start(None, body.claim_ids)

    def _known(claim_id: str) -> dict:
        try:
            return get_claim(holder["env"].engine, claim_id)
        except LookupError:
            raise HTTPException(404, f"unknown claim {claim_id}")

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
