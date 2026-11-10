"""The routes only the agent's side calls, all behind the API key: what its worker did to the records itself before it
went over HTTP. They are left out of the agent's own API document, which holds only the eight operations of the claim
agent's tools."""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Engine

from app import claims as claim_records
from app import configs, evaluations, improvement, runs
from app.auth import Guards, Handler
from app.config import Settings
from app.db import plain
from app.documents import Documents
from app.payments import get_payment
from app.reference import Reference
from app.runs import add_trace_event
from app.settings_store import get_settings as stored_settings, models_allowed
from app.usage import record_model_call, record_work_time


class Step(BaseModel):
    kind: str
    content: dict[str, Any]


class Take(BaseModel):
    run_id: int | None


class Fail(BaseModel):
    attempt: int
    max_attempts: int
    message: str


class CarryOn(BaseModel):
    run_id: int | None


class ModelCall(BaseModel):
    claim_id: str
    run_id: int | None
    model: str
    usage: dict[str, Any]
    round_id: int | None  # the improver's own calls carry its round instead of a claim and a run


class DispatchIn(BaseModel):
    workers: int
    model: str
    reasoning_effort: str | None
    config_version: int | None


class EvaluationIn(DispatchIn):
    eval_set: str
    tiny: bool
    round_id: int | None


class ClaimIds(BaseModel):
    claim_ids: list[str]


class Finish(BaseModel):
    error: str | None


class RoundStartIn(BaseModel):
    handler_id: str


class Stage(BaseModel):
    stage: str
    progress: str | None


class ProposalIn(BaseModel):
    kind: str
    payload: dict[str, Any]
    reason: str


class ProposalDecision(BaseModel):
    approve: bool
    reason: str
    handler_id: str


class Verdict(BaseModel):
    kept: bool
    why: str
    before: dict[str, Any]
    after: dict[str, Any]


class Estimate(BaseModel):
    model: str
    claims: int
    models: dict[str, Any]
    current_model: str


class KeepOrRollBack(BaseModel):
    after: dict[str, Any]
    before: dict[str, Any]
    settings: dict[str, Any]


class WorkTime(BaseModel):
    claim_id: str
    run_id: int | None
    started_at: datetime
    seconds: float


def agent_routes(engine: Engine, ref: Reference, docs: Documents, guards: Guards, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/agent", dependencies=[Depends(guards.agent_key)])

    def known(claim_id: str) -> None:
        try:
            claim_records.claim_state(engine, ref, claim_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc))

    @router.get("/claims/{claim_id}/state", operation_id="get_claim_state")
    def state(claim_id: str):
        """Get where a claim stands, with its decision, payment and whether it needs a sign-off."""
        try:
            return claim_records.claim_state(engine, ref, claim_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc))

    @router.post("/claims/{claim_id}/steps", status_code=201, operation_id="add_claim_step")
    def step(claim_id: str, body: Step):
        """Add one step to a claim's trace."""
        known(claim_id)
        add_trace_event(engine, claim_id, body.kind, body.content)
        return {"claim_id": claim_id}

    @router.post("/claims/{claim_id}/hold", operation_id="hold_claim_payout")
    def hold(claim_id: str):
        """Put a claim's payout on the approvals list, once, and say whether this call did."""
        known(claim_id)
        first_time = get_payment(engine, claim_id) is None
        claim_records.hold_for_sign_off(engine, ref, claim_id)
        return {"first_time": first_time}

    @router.post("/claims/{claim_id}/gateway-refused", operation_id="hold_after_gateway_refusal")
    def gateway_refused(claim_id: str):
        """Hold an approved claim's payout for a handler's sign-off, because the Gateway refused the payment."""
        known(claim_id)
        try:
            return {"first_time": claim_records.hold_after_gateway_refusal(engine, ref, claim_id)}
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @router.post("/claims/{claim_id}/take", operation_id="take_claim")
    def take(claim_id: str, body: Take):
        """Take a waiting claim for a new attempt."""
        try:
            return claim_records.take_claim(engine, claim_id, body.run_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc))

    @router.post("/claims/{claim_id}/fail", operation_id="fail_claim")
    def fail(claim_id: str, body: Fail):
        """Fail an attempt: back in the queue, or left for a handler after the last."""
        known(claim_id)
        return {"status": claim_records.fail_claim(engine, claim_id, body.attempt, body.max_attempts, body.message)}

    @router.post("/claims/{claim_id}/carry-on", operation_id="carry_on_claim")
    def carry_on(claim_id: str, body: CarryOn):
        """Trace a claim's steps under the run that carries it on."""
        known(claim_id)
        claim_records.set_claim_run(engine, claim_id, body.run_id)
        return {"claim_id": claim_id}

    @router.get("/documents/{document}", operation_id="get_agent_document")
    def document(document: str):
        """List the numbered sections of a document."""
        try:
            return [{"section": s.number, "title": s.title} for s in docs.sections(document)]
        except KeyError as exc:
            raise HTTPException(404, exc.args[0])

    @router.post("/model-calls", status_code=201, operation_id="add_model_call")
    def model_call(body: ModelCall):
        """Record one model reply's tokens and cost."""
        record_model_call(engine, body.claim_id, body.run_id, body.model, body.usage, round_id=body.round_id)
        return {"claim_id": body.claim_id}

    @router.post("/work-times", status_code=201, operation_id="add_work_time")
    def work_time(body: WorkTime):
        """Record one stretch of the agent working a claim."""
        record_work_time(engine, body.claim_id, body.run_id, body.started_at, body.seconds)
        return {"claim_id": body.claim_id}

    @router.get("/configuration", operation_id="get_agent_configuration")
    def configuration():
        """Get the current version of the claim agent's configuration."""
        return configs.config_for_run(engine, None)

    @router.get("/runs/{run_id}/configuration", operation_id="get_run_configuration")
    def run_configuration(run_id: int):
        """Get the configuration version a run uses."""
        return configs.config_for_run(engine, run_id)

    # --- runs: what the dispatcher and the service record ---------------------------------------------

    def conflict(code: str, exc: Exception) -> HTTPException:
        return HTTPException(409, {"code": code, "message": str(exc)})

    def start_run(make) -> dict:
        try:
            return {"run_id": make()}
        except runs.RunActive as exc:
            raise conflict("run_active", exc)

    @router.post("/dispatches", status_code=201, operation_id="start_agent_dispatch")
    def start_dispatch(body: DispatchIn):
        """Create a dispatch's run, or refuse while another run is going."""
        return start_run(lambda: runs.start_dispatch(engine, body.workers, body.model, body.reasoning_effort, body.config_version))

    @router.post("/evaluations", status_code=201, operation_id="start_agent_evaluation")
    def start_evaluation(body: EvaluationIn):
        """Create an evaluation's run and its copies, or refuse while another run is going."""
        try:
            return start_run(lambda: evaluations.start_evaluation(
                engine, ref, body.eval_set, body.tiny, body.workers, body.model, body.reasoning_effort,
                config_version=body.config_version, round_id=body.round_id))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @router.get("/runs/running", operation_id="get_running_run")
    def running_run():
        """Get the run that is running, if one is."""
        row = runs.running_run(engine)
        return None if row is None else plain(row)

    @router.post("/runs/stop-running", operation_id="stop_running_runs")
    def stop_running():
        """Stop every run that says it is running: the service that worked them has just started."""
        return runs.stop_running(engine)

    @router.get("/claims-to-dispatch", operation_id="get_claims_to_dispatch")
    def claims_to_dispatch():
        """List the claims a dispatch works, in order."""
        return runs.claims_to_dispatch(engine)

    @router.get("/runs/{run_id}", operation_id="get_agent_run")
    def get_run(run_id: int):
        """Get one run."""
        row = runs.get_run(engine, run_id)
        if row is None:
            raise HTTPException(404, f"no run {run_id}")
        return plain(row)

    @router.get("/runs/{run_id}/claims", operation_id="get_run_claim_ids")
    def run_claim_ids(run_id: int):
        """List a run's claims, in order."""
        return runs.run_claim_ids(engine, run_id)

    @router.post("/runs/{run_id}/claims", status_code=201, operation_id="add_run_claims")
    def add_run_claims(run_id: int, body: ClaimIds):
        """Add the claims a dispatch works to its run."""
        runs.add_run_claims(engine, run_id, body.claim_ids)
        return {"run_id": run_id}

    @router.post("/runs/{run_id}/claims/{claim_id}/started", operation_id="claim_started")
    def claim_started(run_id: int, claim_id: str):
        """Say a claim agent took the claim in this run."""
        runs.claim_started(engine, run_id, claim_id)
        return {"run_id": run_id}

    @router.post("/runs/{run_id}/claims/{claim_id}/finished", operation_id="claim_finished")
    def claim_finished(run_id: int, claim_id: str):
        """Say a claim agent is done with the claim in this run."""
        runs.claim_finished(engine, run_id, claim_id)
        return {"run_id": run_id}

    @router.post("/runs/{run_id}/finish", operation_id="finish_agent_run")
    def finish_run(run_id: int, body: Finish):
        """End a run. One that ends with no error is graded first, if it is an evaluation."""
        runs.finish_run(engine, run_id, error=body.error, settings=settings if body.error is None else None)
        return {"run_id": run_id}

    # --- rounds: what the improver records --------------------------------------------------------------

    def one_round(round_id: int):
        try:
            return improvement.get_round(engine, round_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc))

    def stopped(call):
        try:
            return call()
        except improvement.RoundStopped as exc:
            raise conflict("round_stopped", exc)
        except LookupError as exc:
            raise HTTPException(404, str(exc))

    @router.post("/rounds", status_code=201, operation_id="round_start")
    def round_start(body: RoundStartIn):
        """Open a round for a handler, working to the settings as they stand and the current recipe."""
        if next((h for h in ref.handlers if h["handler_id"] == body.handler_id), None) is None:
            raise HTTPException(404, f"unknown handler {body.handler_id}")
        try:
            return {"round_id": improvement.start_round(engine, body.handler_id, configs.current(engine, "recipe")["version"],
                                                        stored_settings(engine))}
        except improvement.RoundOpen as exc:
            raise conflict("round_open", exc)

    @router.post("/rounds/stop-working", operation_id="round_stop_working")
    def round_stop_working():
        """End the rounds that lost their process; one waiting for a handler is still waiting."""
        return improvement.stop_working_rounds(engine)

    @router.get("/rounds/{round_id}", operation_id="round_one")
    def round_get(round_id: int):
        """Get one round's row."""
        return plain(one_round(round_id))

    @router.get("/rounds/{round_id}/spent", operation_id="round_spent")
    def round_spent(round_id: int):
        """Get what a round has spent so far."""
        return {"spent": improvement.round_spent(engine, round_id)}

    @router.get("/rounds/{round_id}/view", operation_id="round_improver_view")
    def round_view(round_id: int):
        """Get what the improver reads in a round."""
        return stopped(lambda: improvement.improver_view(engine, ref, settings, round_id))

    @router.post("/rounds/{round_id}/stage", operation_id="round_set_stage")
    def round_stage(round_id: int, body: Stage):
        """Say where a round has got to. A round that has ended refuses it."""
        stopped(lambda: improvement.set_stage(engine, round_id, body.stage, body.progress))
        return {"round_id": round_id}

    @router.post("/rounds/{round_id}/proposals", status_code=201, operation_id="round_add_proposal")
    def round_add_proposal(round_id: int, body: ProposalIn):
        """Record what the improver proposes, and put the round to waiting for a handler."""
        return {"id": stopped(lambda: improvement.add_proposal(engine, round_id, body.kind, body.payload, body.reason))}

    @router.post("/rounds/{round_id}/verdict", operation_id="round_record_verdict")
    def round_verdict(round_id: int, body: Verdict):
        """Record whether the version was kept."""
        stopped(lambda: improvement.record_verdict(engine, round_id, body.kept, body.why, body.before, body.after))
        return {"round_id": round_id}

    @router.post("/rounds/{round_id}/finish", operation_id="round_finish")
    def round_finish(round_id: int, body: Finish):
        """End a round, with what went wrong if something did."""
        improvement.finish_round(engine, round_id, error=body.error)
        return {"round_id": round_id}

    @router.get("/proposals/{proposal_id}", operation_id="round_get_proposal")
    def get_proposal(proposal_id: int):
        """Get one proposal."""
        try:
            return plain(improvement.get_proposal(engine, proposal_id))
        except LookupError as exc:
            raise HTTPException(404, str(exc))

    @router.post("/proposals/{proposal_id}/decision", operation_id="round_decide_proposal")
    def decide_proposal(proposal_id: int, body: ProposalDecision):
        """Record a handler's approval or rejection of a proposal."""
        row = next((h for h in ref.handlers if h["handler_id"] == body.handler_id), None)
        if row is None:
            raise HTTPException(404, f"unknown handler {body.handler_id}")
        handler = Handler(row["handler_id"], row["name"], row["role"], row["email"], bool(row["can_approve_over_limit"]))
        try:
            return improvement.decide_proposal(engine, proposal_id, body.approve, body.reason, handler)
        except improvement.RoundStopped as exc:
            raise conflict("round_stopped", exc)
        except improvement.AlreadyDecided as exc:
            raise conflict("already_decided", exc)
        except LookupError as exc:
            raise HTTPException(404, str(exc))

    @router.get("/versions/{kind}/current", operation_id="round_current_version")
    def current_version(kind: configs.Kind):
        """Get the current version of the configuration or the recipe."""
        return configs.current(engine, kind)

    @router.get("/versions/{kind}/{version}", operation_id="round_get_version")
    def get_version(kind: configs.Kind, version: int):
        """Get one version of the configuration or the recipe."""
        try:
            return configs.get_version(engine, kind, version)
        except LookupError as exc:
            raise HTTPException(404, str(exc))

    @router.get("/scores/{version}", operation_id="round_version_scores")
    def version_scores(version: int):
        """Get a version's scores per set."""
        return improvement.version_scores(engine, version)

    @router.get("/owed-evaluations/{version}", operation_id="round_needs_first_evaluations")
    def owed(version: int):
        """List the evaluations a version still needs before a round can compare against it."""
        return improvement.needs_first_evaluations(engine, version)

    @router.get("/sets/{eval_set}/size", operation_id="round_set_size")
    def set_size(eval_set: str):
        """Get how many claims a whole evaluation of a set works."""
        return {"size": improvement.set_size(engine, eval_set)}

    @router.post("/estimate", operation_id="round_estimate")
    def estimate(body: Estimate):
        """Estimate what working some claims on a model costs."""
        return {"cost": improvement.estimate(engine, body.model, body.claims, body.models, body.current_model)}

    @router.post("/keep-or-roll-back", operation_id="round_keep_or_roll_back")
    def keep_or_roll_back(body: KeepOrRollBack):
        """Apply the rule that keeps or rolls back a version."""
        kept, why = improvement.keep_or_roll_back(body.after, body.before, body.settings)
        return {"kept": kept, "why": why}

    @router.get("/platform-settings", operation_id="round_get_settings")
    def platform_settings():
        """Get every setting's current value."""
        return stored_settings(engine)

    @router.get("/models-allowed", operation_id="round_models_allowed")
    def allowed_models():
        """Get the models the claim agent may use."""
        return models_allowed(ref, settings)

    return router
