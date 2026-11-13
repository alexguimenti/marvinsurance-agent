import json
import logging
import re
from pathlib import Path
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, Literal, Mapping

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app import agent_client, claims as claim_records, configs, evaluations as evaluation_runs, improvement, runs
from app.work import WorkGoing, start_work
from app.agent_routes import agent_routes
from app.auth import Handler, handler_view, make_guards, make_jwks
from app.config import Settings
from app.copilot_routes import copilot_routes
from app.db import (
    claims,
    decisions,
    init_schema,
    iso,
    make_engine,
    payments,
    plain,
    sent_back_decisions,
    trace_events,
    warm_up,
)
from app.documents import Documents
from app.payments import NotWaiting, OverLimit, SignOffRequired, pay_claim as pay_the_claim, sign_off
from app.payout import LineItem, calculate_payout
from app.section14 import Section14Refused, request_problem
from app.send_back import send_back as send_the_claim_back
from app.reference import Reference
from app.scoring import evaluation_report, list_evaluations
from app.improvement import get_round as get_round_row, list_rounds as list_round_rows, round_report, stop_round as stop_round_row, version_scores
from app.seed import seed_claims
from app.settings_store import approval_limit, change_setting, get_settings as stored_settings, models_allowed, setting_history
from app.usage import claim_totals, run_totals

# The operations the claim agent's tools are made of. The Gateway makes a tool of every operation in the document it is
# given, so the document holds these and nothing else: not the dashboard's routes, the evaluations or the sign-off.
GATEWAY_OPERATIONS = frozenset({
    "get_claim", "get_policy", "get_claim_history", "calculate_payout", "record_decision", "pay_claim",
})

log = logging.getLogger(__name__)


class PayoutBody(BaseModel):
    items: list[LineItem]
    third_party_paid: float = Field(description="What a carrier or another party has paid for the same loss, or 0 when nothing was paid")
    deductible: float = Field(description="The deductible that applies to this benefit, or 0 when none applies")
    # 0, not null, when there is no maximum: the Gateway cannot read "a number or null"
    benefit_max: float = Field(description="The benefit's maximum payout, or 0 when the benefit has no maximum")

    @field_validator("benefit_max", "third_party_paid", "deductible")
    @classmethod
    def not_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("this is 0 (none) or more")
        return value


class DecisionIn(BaseModel):
    decision: Literal["approve", "decline", "refer"]
    payout: float
    rationale: str
    citations: list[str]
    decided_by: str  # who or what made the decision
    model: str


class PaymentIn(BaseModel):
    amount: float


class SendBack(BaseModel):
    note: str = Field(min_length=1, max_length=1000)  # what the handler wants the agent to look at again


class SettingValue(BaseModel):
    value: Any


class DispatchBody(BaseModel):
    workers: int = Field(ge=1, le=25)  # claim agents at once


class DecisionBody(BaseModel):
    proposal_id: int
    approve: bool
    reason: str  # a rejection's reason goes back to the improver


class EvaluationBody(BaseModel):
    eval_set: Literal["evaluation", "held_out"]
    tiny: bool  # only the set's five tiny claims
    workers: int = Field(ge=1, le=25)


def _decision(row: Mapping | None) -> dict | None:
    if row is None:
        return None
    return {
        "decision": row["decision"],
        "payout": row["payout"],
        "calculation": claim_records.calculation_of(row),
        "rationale": row["rationale"],
        "citations": json.loads(row["citations"]),
        "model": row["model"],
        "decided_by": row["decided_by"],
        "decided_at": iso(row["created_at"]),
        "manual_version": row["manual_version"],
        "wording_version": row["wording_version"],
        "created_at": iso(row["created_at"]),
    }


def _signed_off(decision: dict | None, payment: dict | None) -> dict | None:
    """A payout a handler signed off carries who signed it off and when, on its decision record."""
    if decision is None:
        return None
    signed = payment is not None and payment["status"] == "paid" and payment.get("approved_by")
    return {**decision, "signed_off_by": (payment["approved_by_name"] or payment["approved_by"]) if signed else None,
            "signed_off_at": payment["approved_at"] if signed else None}


def _run(row: Mapping | None) -> dict | None:
    return None if row is None else plain(row)


def create_app(settings: Settings, http=httpx, jwks=None) -> FastAPI:
    ref = Reference(settings.data_dir)
    guards = make_guards(ref, jwks if jwks is not None else make_jwks(settings), settings.api_key)
    signed_in, senior = guards.signed_in, guards.senior
    engine = make_engine(settings, max_connections=4)
    docs = Documents(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_schema(engine)
        seed_claims(engine, ref)
        warm_up(engine, 4)
        yield
        engine.dispose()

    # a public address publishes no schema: the agent's own document is served by a route, behind the key
    app = FastAPI(title="Marvin Agent", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    # Every route needs a signed-in handler, unless it is in one of the others
    router = APIRouter(prefix="/api", dependencies=[Depends(signed_in)])
    shared = APIRouter(prefix="/api", dependencies=[Depends(guards.key_or_handler)])  # the reads and tools the agent shares
    public = APIRouter(prefix="/api")
    agent_only = APIRouter(prefix="/api", dependencies=[Depends(guards.agent_key)])  # the claim agent's two writes

    def one(table, key, value) -> Mapping | None:
        with engine.connect() as conn:
            return conn.execute(select(table).where(key == value)).mappings().first()

    def claim_row(claim_id: str) -> Mapping:
        row = one(claims, claims.c.claim_id, claim_id)
        if row is None:
            raise HTTPException(404, f"unknown claim {claim_id}")
        return row

    def claim_row_payload(claim_id: str) -> dict:
        try:
            return claim_records.get_claim_payload(engine, claim_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc))

    def trace_rows(claim_id: str, kind: str | None = None) -> list[Mapping]:
        query = select(trace_events).where(trace_events.c.claim_id == claim_id).order_by(trace_events.c.seq)
        if kind:
            query = query.where(trace_events.c.kind == kind)
        with engine.connect() as conn:
            return conn.execute(query).mappings().all()

    def context_used(rows: list[Mapping]) -> list[dict]:
        """What the agent looked up in the context layer, once each, grouped by the file it came from."""
        files: dict[str, dict] = {}
        for r in rows:
            step = json.loads(r["content"])
            lookup = {"tool": str(step["tool"]).split("___")[-1], "args": step.get("args", {})}
            for source in step.get("sources", []):
                entry = files.setdefault(source["file"], {**source, "lookups": []})
                if lookup not in entry["lookups"]:
                    entry["lookups"].append(lookup)
        return list(files.values())

    def sent_back_view(claim_id: str) -> list[dict]:
        """Each decision sent back on a claim, newest first, with its note, who sent it back and when."""
        with engine.connect() as conn:
            rows = conn.execute(select(sent_back_decisions).where(sent_back_decisions.c.claim_id == claim_id)
                                .order_by(sent_back_decisions.c.id.desc())).mappings().all()
        out = []
        for r in rows:
            handler = next((h for h in ref.handlers if h["handler_id"] == r["sent_back_by"]), None)
            out.append({"decision": r["decision"], "payout": r["payout"], "calculation": claim_records.calculation_of(r),
                        "rationale": r["rationale"], "citations": json.loads(r["citations"]), "decided_by": r["decided_by"],
                        "decided_at": iso(r["decided_at"]), "note": r["note"], "sent_back_by": r["sent_back_by"],
                        "sent_back_by_name": handler["name"] if handler else r["sent_back_by"], "sent_back_at": iso(r["sent_back_at"])})
        return out

    def payment_view(payment: Mapping | None) -> dict | None:
        if payment is None:
            return None
        handler = next((h for h in ref.handlers if h["handler_id"] == payment["approved_by"]), None)
        return {**plain(payment), "approved_by_name": handler["name"] if handler else None}

    def version_scores_view(version: int) -> dict:
        """A version's scores per set, the averages of its rounds' whole-set evaluations. None until a round has run one."""
        return version_scores(engine, version)

    def hand_over(payload: dict, undo, **ids) -> dict:
        """Ask the agent to work what the app has just created. When the agent did not take it (it refused, or the request never
        got there, or the agent answered with an error) the app undoes it; when the call may have started it (a timeout, an answer
        cut off) the app changes nothing. The handler is told as they were before: a refusal as a 409, an agent that was not
        reached as a 503, and a call that may have started the work as a 504 that says to reload the page."""
        try:
            agent_client.send(settings, payload, http)
        except agent_client.AgentRefused as exc:
            undo(exc.reason)
            raise HTTPException(409, exc.reason)
        except agent_client.AgentNotReached as exc:
            undo(str(exc))
            if exc.status:  # the agent answered, with an error: the handler is told what it said
                raise HTTPException(exc.status, str(exc))
            raise HTTPException(503, "the agent is not running")
        except agent_client.AgentMaybe:
            raise HTTPException(504, "The agent did not answer in time. Reload the page to see whether it went through.")
        return {"status": "started", **ids}

    def begin(create) -> int:
        """Create a piece of the agent's work if nothing is going: a 409 names what is."""
        try:
            return start_work(engine, create)
        except WorkGoing as exc:
            raise HTTPException(409, str(exc))

    def current_label() -> tuple[int, str, str]:
        """The configuration version a new run uses, and the model and reasoning effort it runs with."""
        config = configs.config_for_run(engine, None)
        if settings.agent_provider == "scripted":  # no model is called: the scripted one answers for every version
            return config["version"], "scripted", "default"
        return config["version"], config["model"], config["reasoning_effort"]

    @public.get("/health", operation_id="get_health")
    def health():
        """Say that the backend is up."""
        return {"status": "ok"}

    @router.get("/claims", operation_id="list_claims")
    def list_claims():
        """List every claim with its status and decision."""
        query = (
            select(claims.c.claim_id, claims.c.status, claims.c.attempts, claims.c.payload,
                   decisions.c.decision, decisions.c.payout)
            .select_from(claims.outerjoin(decisions, claims.c.claim_id == decisions.c.claim_id))
            .where(claims.c.copy_of.is_(None))  # an evaluation's copies never show in the queue
            .order_by(claims.c.claim_id)
        )
        with engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        out = []
        for r in rows:
            claim = json.loads(r["payload"])
            out.append({
                "claim_id": r["claim_id"],
                "claim_type": claim["claim_type"],
                "summary": claim["summary"],
                "customer_name": ref.customer(claim["customer_id"])["name"],
                "amount_claimed": claim["amount_claimed"],
                "status": r["status"],
                "attempts": r["attempts"],
                "decision": r["decision"],
                "payout": r["payout"],
            })
        return out

    @shared.get("/claims/{claim_id}", operation_id="get_claim")
    def get_claim(claim_id: str):
        """Get one claim with its policy, customer, decision, payment, cost and what the agent read."""
        with engine.connect() as conn:  # one connection: this page is asked for every second or two
            row = conn.execute(select(claims).where(claims.c.claim_id == claim_id)).mappings().first()
            if row is None:
                raise HTTPException(404, f"unknown claim {claim_id}")
            decision = conn.execute(select(decisions).where(decisions.c.claim_id == claim_id)).mappings().first()
            payment = conn.execute(select(payments).where(payments.c.claim_id == claim_id)).mappings().first()
            steps = conn.execute(
                select(trace_events.c.kind, trace_events.c.content)
                .where(trace_events.c.claim_id == claim_id, trace_events.c.kind == "lookup")
                .order_by(trace_events.c.seq)
            ).mappings().all()
        usage = claim_totals(engine, claim_id)
        claim = json.loads(row["payload"])
        return {
            "claim": claim,
            "status": row["status"],
            "attempts": row["attempts"],
            "last_error": row["last_error"],
            "copy_of": row["copy_of"],
            "evaluation_id": row["evaluation_id"],
            "policy": ref.policy(claim["policy_id"]),
            "customer": ref.customer(claim["customer_id"]),
            "decision": _signed_off(_decision(decision), payment_view(payment)),
            "payment": payment_view(payment),
            "usage": usage,
            "context_used": context_used(steps),
            "sent_back": sent_back_view(claim_id),
            "waiting_for_next_dispatch": row["status"] == "sent_back",
        }

    @shared.get("/claims/{claim_id}/policy", operation_id="get_policy")
    def get_policy(claim_id: str):
        """Get the policy for the claim's trip: plan, add-ons, purchase date, trip dates and travelers."""
        return ref.policy(claim_row_payload(claim_id)["policy_id"])

    @shared.get("/claims/{claim_id}/history", operation_id="get_claim_history")
    def get_claim_history(claim_id: str):
        """Get the claim's customer: name, customer-since date and earlier claims."""
        customer = ref.customer(claim_row_payload(claim_id)["customer_id"])
        return {"name": customer["name"], "customer_since": customer["customer_since"],
                "previous_claims": customer["previous_claims"]}

    @agent_only.post("/claims/{claim_id}/decision", operation_id="record_decision")
    def record_decision(claim_id: str, body: DecisionIn):
        """Record the claim's decision, once: approve, decline or refer, with the payout, the section and the reason."""
        problem = request_problem(body)  # the same answer on any claim
        if problem:
            raise HTTPException(422, problem)
        claim_row(claim_id)
        try:
            return claim_records.record_decision(engine, ref, claim_id, body.model_dump(exclude={"model"}), body.model)
        except Section14Refused as exc:
            raise HTTPException(422, str(exc))
        except claim_records.DecisionConflict as exc:
            raise HTTPException(409, str(exc))

    @agent_only.post("/claims/{claim_id}/pay", operation_id="pay_claim")
    def pay_claim(claim_id: str, body: PaymentIn):
        """Pay a claim's approved amount, once."""
        try:
            return pay_the_claim(engine, ref, claim_id, body.amount)
        except OverLimit as exc:
            raise HTTPException(403, str(exc))
        except SignOffRequired as exc:
            raise HTTPException(403, str(exc))
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @shared.get("/agent/openapi.json", operation_id="get_agent_openapi")
    def agent_openapi():
        """Get the OpenAPI document of the claim agent's six operations."""
        wanted = [r for each in (shared, agent_only) for r in each.routes if getattr(r, "operation_id", None) in GATEWAY_OPERATIONS]
        document = get_openapi(title="Marvin Agent: the claim agent's operations", version="1.0.0", routes=wanted)
        document.pop("servers", None)  # the deployment adds the app's address
        for operations in document["paths"].values():  # how a request is checked is not an input of the operation
            for operation in operations.values():
                operation.pop("security", None)
        document.get("components", {}).pop("securitySchemes", None)
        return document

    @router.get("/claims/{claim_id}/trace", operation_id="get_claim_trace")
    def get_trace(claim_id: str):
        """List every step recorded on a claim, in order."""
        claim_row(claim_id)
        return [{"seq": r["seq"], "kind": r["kind"], "content": json.loads(r["content"]),
                 "created_at": iso(r["created_at"])} for r in trace_rows(claim_id)]

    @router.post("/dispatches", status_code=202, operation_id="start_dispatch")
    def start_dispatch(body: DispatchBody, handler: Handler = Depends(signed_in)):
        """Start a dispatch of every claim in progress and every waiting claim, with N claim agents at once."""
        version, model, effort = current_label()
        run_id = begin(lambda: runs.start_dispatch(engine, body.workers, model, effort, version))
        return hand_over({"operation": "work_run", "run_id": run_id}, lambda why: runs.stop_run(engine, run_id, why), run_id=run_id)

    @router.post("/evaluations", status_code=202, operation_id="start_evaluation")
    def start_evaluation(body: EvaluationBody, handler: Handler = Depends(signed_in)):
        """Start an evaluation on copies of a labelled set, whole or its tiny claims, with N claim agents at once."""
        version, model, effort = current_label()
        run_id = begin(lambda: evaluation_runs.start_evaluation(engine, ref, body.eval_set, body.tiny, body.workers, model, effort,
                                                            config_version=version))
        return hand_over({"operation": "work_run", "run_id": run_id}, lambda why: runs.stop_run(engine, run_id, why), run_id=run_id)

    @router.post("/rounds", status_code=202, operation_id="start_round")
    def start_round(handler: Handler = Depends(senior)):
        """Start an improvement round. Only a handler who can sign off over the limit may."""
        def create() -> int:
            try:
                return improvement.start_round(engine, handler.handler_id, configs.current(engine, "recipe")["version"], stored_settings(engine))
            except improvement.RoundOpen as exc:
                raise WorkGoing(str(exc).removesuffix(" is already open")) from exc

        round_id = begin(create)
        return hand_over({"operation": "work_round", "round_id": round_id},
                         lambda why: improvement.stop_round_with_reason(engine, round_id, why), round_id=round_id)

    @router.post("/rounds/{round_id}/stop", operation_id="stop_round")
    def stop_round(round_id: int, handler: Handler = Depends(senior)):
        """Stop a round that is open. What its verdict already decided stays."""
        try:
            get_round_row(engine, round_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        if not stop_round_row(engine, round_id, handler.handler_id):
            raise HTTPException(409, f"round {round_id} has ended")
        return {"round_id": round_id, "stage": "stopped"}

    @router.post("/rounds/{round_id}/decision", status_code=202, operation_id="decide_proposal")
    def decide_proposal_route(round_id: int, body: DecisionBody, handler: Handler = Depends(senior)):
        """Approve or reject the proposal a round waits on, as the signed-in handler, and ask the agent to carry the round on."""
        def record() -> int:
            try:
                if improvement.get_proposal(engine, body.proposal_id)["round_id"] != round_id:
                    raise LookupError(f"proposal {body.proposal_id} is not in round {round_id}")
                improvement.decide_proposal(engine, body.proposal_id, body.approve, body.reason, handler)
            except LookupError as exc:
                raise HTTPException(404, str(exc))
            except (improvement.RoundStopped, improvement.AlreadyDecided) as exc:
                raise HTTPException(409, str(exc))
            return body.proposal_id

        proposal_id = begin(record)
        return hand_over({"operation": "carry_round_on", "round_id": round_id, "proposal_id": proposal_id},
                         lambda why: improvement.clear_review(engine, proposal_id), round_id=round_id)

    @router.get("/rounds", operation_id="list_rounds")
    def rounds_list():
        """List the improvement rounds, newest first, each with where it has got to and what it cost."""
        return [round_report(engine, ref, r) for r in list_round_rows(engine)]

    @router.get("/rounds/{round_id}", operation_id="get_round")
    def round_one(round_id: int):
        """Get one round with its proposals, the one it waits on shown as a diff, and what it cost."""
        try:
            return round_report(engine, ref, get_round_row(engine, round_id))
        except LookupError as exc:
            raise HTTPException(404, str(exc))

    def run_report(row: Mapping) -> dict:
        """A run with its claims, its totals and, once it has ended, its summary from the records."""
        return {
            "run": _run(row),
            "claims": runs.run_claims_view(engine, row["run_id"]),
            "totals": run_totals(engine, row["run_id"]),
            "summary": None if row["status"] == "running" else runs.run_summary(engine, row["run_id"]),
        }

    @router.get("/dispatches/latest", operation_id="get_latest_dispatch")
    def latest_dispatch():
        """Get the latest dispatch with its claims, its cost and time and, once it has ended, its summary."""
        row = runs.latest_run(engine, "dispatch")
        return run_report(row) if row else {"run": None, "claims": [], "totals": None, "summary": None}

    @router.get("/dispatches/{run_id}", operation_id="get_dispatch")
    def get_dispatch(run_id: int):
        """Get one dispatch by its id, with its claims, its cost and time and, once it has ended, its summary."""
        row = runs.get_run(engine, run_id)
        if row is None or row["kind"] != "dispatch":
            raise HTTPException(404, f"no dispatch {run_id}")
        return run_report(row)

    @router.get("/customers", operation_id="find_customers")
    def find_customers(name: str = Query(min_length=1)):
        """Find customers by part of their name, or by their exact id."""
        wanted = name.strip().lower()
        found = [c for c in ref.read("customers.json") if wanted in c["name"].lower() or wanted == c["customer_id"].lower()]
        return [{"customer_id": c["customer_id"], "name": c["name"], "customer_since": c["customer_since"]} for c in found]

    @router.get("/customers/{customer_id}", operation_id="get_customer")
    def get_customer(customer_id: str):
        """Get a customer with their policies, their earlier claims and their claims in our queue."""
        try:
            customer = ref.customer(customer_id)
        except LookupError:
            raise HTTPException(404, f"unknown customer {customer_id}")
        query = (select(claims.c.claim_id, claims.c.status, claims.c.payload, decisions.c.decision, decisions.c.payout)
                 .select_from(claims.outerjoin(decisions, claims.c.claim_id == decisions.c.claim_id))
                 .where(claims.c.copy_of.is_(None)).order_by(claims.c.claim_id))
        with engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        queued = [{"claim_id": r["claim_id"], "status": r["status"], "decision": r["decision"], "payout": r["payout"]}
                  for r in rows if json.loads(r["payload"])["customer_id"] == customer_id]
        return {"customer": customer, "policies": [p for p in ref.read("policies.json") if p["customer_id"] == customer_id],
                "previous_claims": customer["previous_claims"], "queue_claims": queued}

    @router.get("/policies/{policy_id}", operation_id="get_policy_by_id")
    def get_policy_by_id(policy_id: str):
        """Get a policy by its id: plan, add-ons, purchase date, trip dates and travelers."""
        try:
            return ref.policy(policy_id)
        except LookupError:
            raise HTTPException(404, f"unknown policy {policy_id}")

    @router.get("/payouts/over-limit", operation_id="count_payouts_over_limit")
    def count_payouts_over_limit():
        """Count the claims in our queue paid more than the approval limit, and how many of those carry a handler's sign-off."""
        limit = approval_limit(engine, ref)
        query = (select(payments.c.approved_by).select_from(payments.join(claims, payments.c.claim_id == claims.c.claim_id))
                 .where(payments.c.status == "paid", payments.c.amount > limit, claims.c.copy_of.is_(None)))
        with engine.connect() as conn:
            approvers = [r[0] for r in conn.execute(query)]
        handlers_ids = {h["handler_id"] for h in ref.handlers}
        return {"approval_limit": limit, "paid_over_limit": len(approvers),
                "signed_off": sum(1 for a in approvers if a in handlers_ids)}

    @router.get("/evaluations", operation_id="list_evaluations")
    def evaluations():
        """List the evaluations, newest first, each with its measures."""
        return list_evaluations(engine)

    @router.get("/evaluations/{run_id}", operation_id="get_evaluation")
    def evaluation(run_id: int):
        """Get one evaluation with its measures and its claims, each beside the handler's decision."""
        report = evaluation_report(engine, run_id)
        if report is None:
            raise HTTPException(404, f"no evaluation {run_id}")
        return report

    @router.get("/configuration", operation_id="get_configuration")
    def configuration():
        """Show the current version of the claim agent's configuration, every version and the recipe's versions."""
        names = {h["handler_id"]: h["name"] for h in ref.handlers}

        def named(row: dict) -> dict:
            return {**row, "approved_by_name": names.get(row["approved_by"])}

        return {
            "current": named(configs.current(engine, "config")),
            "current_recipe": configs.current(engine, "recipe")["version"],
            "versions": [{**named(v), "scores": version_scores_view(v["version"])} for v in configs.list_versions(engine, "config")],
            "recipes": [named(r) for r in configs.list_versions(engine, "recipe")],
        }

    @router.get("/configuration/{kind}/{version}/diff", operation_id="get_version_diff")
    def version_diff(kind: Literal["config", "recipe"], version: int):
        """Show what a version changed from the one before it, line by line."""
        try:
            row = configs.get_version(engine, kind, version)
            before = configs.get_version(engine, kind, version - 1) if version > 1 else None
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        text = "instructions" if kind == "config" else "recipe"
        out = {"kind": kind, "version": version, "against": before["version"] if before else None, "summary": row["summary"],
               "lines": configs.diff(before[text] if before else "", row[text])}
        if kind == "config":
            before_cfg = before or {"model": None, "reasoning_effort": None}
            out["model"] = {"before": before_cfg["model"], "after": row["model"]}
            out["reasoning_effort"] = {"before": before_cfg["reasoning_effort"], "after": row["reasoning_effort"]}
        return out

    @shared.post("/claims/{claim_id}/payout", operation_id="calculate_payout")
    def payout(claim_id: str, body: PayoutBody):
        """Work out the claim's payout under wording section 10, and say whether it needs a sign-off."""
        claim_row(claim_id)
        result = calculate_payout(body.items, body.third_party_paid, body.deductible, body.benefit_max,
                                  approval_limit(engine, ref))
        claim_records.record_calculation(engine, claim_id, result["payout"], result["steps"])  # an approval must match it
        return result

    @router.get("/approvals", operation_id="list_approvals")
    def approvals():
        """List the payouts waiting for a handler's sign-off."""
        query = (
            select(payments, claims.c.payload, decisions.c.rationale)
            .select_from(
                payments.join(claims, payments.c.claim_id == claims.c.claim_id)
                .outerjoin(decisions, payments.c.claim_id == decisions.c.claim_id)
            )
            .where(payments.c.status == "held", claims.c.copy_of.is_(None))  # nobody signs off a copy
            .order_by(payments.c.claim_id)
        )
        with engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        out = []
        for r in rows:
            claim = json.loads(r["payload"])
            out.append({
                "payment_id": r["payment_id"],
                "claim_id": r["claim_id"],
                "amount": r["amount"],
                "status": r["status"],
                "customer_name": ref.customer(claim["customer_id"])["name"],
                "claim_type": claim["claim_type"],
                "rationale": r["rationale"],
                "hold_reason": r["hold_reason"],
            })
        return out

    @router.get("/settings", operation_id="get_platform_settings")
    def platform_settings():
        """Show every setting with who last changed it and when, and the models the claim agent may use."""
        names = {h["handler_id"]: h["name"] for h in ref.handlers}
        rows = [{**r, "changed_by_name": names.get(r["changed_by"])} for r in setting_history(engine, current_only=True)]
        return {"settings": rows, "models_allowed": models_allowed(ref, settings)}

    @router.put("/settings/{name}", operation_id="change_platform_setting")
    def change_platform_setting(name: str, body: SettingValue, handler: Handler = Depends(senior)):
        """Change one setting, recording who changed it. Only a handler who can sign off over the limit may."""
        try:
            return change_setting(engine, name, body.value, handler)
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @router.get("/me", operation_id="get_signed_in_handler")
    def me(handler: Handler = Depends(signed_in)):
        """Say who is signed in and whether they can sign off payouts over the limit."""
        return handler_view(handler)

    def ask_the_agent_to_resume(claim_id: str) -> None:
        """Ask the agent to resume a claim whose answer the app has just recorded. Whatever happens to the call, what was
        recorded stands: a failure is logged, and the claim waits for the agent's next dispatch or a resume."""
        try:
            agent_client.resume_claim(settings, claim_id, http)
        except Exception as exc:
            log.warning("could not ask the agent to resume %s: %s", claim_id, exc)

    @router.post("/claims/{claim_id}/send-back", operation_id="send_back_claim")
    def send_back_route(claim_id: str, body: SendBack, handler: Handler = Depends(signed_in)):
        """Send a claim waiting for sign-off back to the agent with a note, and ask the agent to decide it again."""
        try:
            send_the_claim_back(engine, ref, claim_id, body.note, handler.handler_id)
        except LookupError:
            raise HTTPException(404, f"unknown claim {claim_id}")
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        except NotWaiting as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        ask_the_agent_to_resume(claim_id)  # a send back it never receives is worked by the next dispatch
        return get_claim(claim_id)

    @router.post("/payments/{payment_id}/approve", operation_id="sign_off_payment")
    def approve(payment_id: str, handler: Handler = Depends(signed_in)):
        """Record a handler's sign-off and pay the claim, once."""
        try:
            payment = sign_off(engine, ref, payment_id, handler.handler_id)
        except LookupError:
            raise HTTPException(404, f"unknown payment {payment_id}")
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        except NotWaiting as exc:
            raise HTTPException(409, str(exc))
        ask_the_agent_to_resume(payment.claim_id)
        return {**asdict(payment), "claim": get_claim(payment.claim_id)}

    app.include_router(public)
    app.include_router(agent_only)
    app.include_router(agent_routes(engine, ref, docs, guards, settings))
    app.include_router(shared)
    app.include_router(copilot_routes(engine, ref, guards, settings, http))
    app.include_router(router)

    dist = Path(settings.dashboard_dir).resolve()
    if dist.is_dir():  # the dashboard's build: its files at their own paths, and its pages on a refresh
        @app.get("/{path:path}", include_in_schema=False)
        def dashboard(path: str):
            if path == "api" or path.startswith("api/"):
                raise HTTPException(404, "not found")  # an unknown API path stays a JSON 404
            target = (dist / path).resolve()
            if path and target.is_relative_to(dist) and target.is_file():  # nothing outside the build is ever served
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app
