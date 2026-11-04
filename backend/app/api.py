import json
import re
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Mapping

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from agent.prompts import claim_prompt, load_instructions
from app import agent_client, runs
from app.config import Settings
from app.db import (
    claims,
    decisions,
    init_schema,
    iso,
    make_engine,
    payments,
    plain,
    trace_events,
)
from app.documents import documents_for
from app.payments import sign_off
from app.payout import LineItem, calculate_payout
from app.reference import Reference
from app.seed import seed_claims

# How the claim agent traces a section it read: "[claims_manual section 7: Interpretation notes, 5000 characters]".
# Refused reads (errors, rereads, past the cap) are traced as plain text and do not match.
_SECTION_READ = re.compile(r"^\[(\w+) section (\d+): .+, \d+ characters\]$")


class ApproveBody(BaseModel):
    handler_id: str


class PayoutBody(BaseModel):
    items: list[LineItem]
    third_party_paid: float
    deductible: float
    benefit_max: float | None


class RunLimit(BaseModel):
    limit: int = Field(ge=1)


class SelectedClaims(BaseModel):
    claim_ids: list[str] = Field(min_length=1)


def _decision(row: Mapping | None) -> dict | None:
    if row is None:
        return None
    return {
        "decision": row["decision"],
        "payout": row["payout"],
        "calculation": json.loads(row["calculation"]),
        "rationale": row["rationale"],
        "citations": json.loads(row["citations"]),
        "model": row["model"],
        "manual_version": row["manual_version"],
        "wording_version": row["wording_version"],
        "created_at": iso(row["created_at"]),
    }


def _run(row: Mapping | None) -> dict | None:
    if row is None:
        return None
    out = {k: row[k] for k in ("run_id", "status", "cap", "model", "summary", "error")}
    out["started_at"], out["finished_at"] = iso(row["started_at"]), iso(row["finished_at"])
    out["usage"] = json.loads(row["usage"]) if row["usage"] else None
    out["alive"] = row["status"] == "running" and runs.is_alive(row, datetime.now(timezone.utc))
    return out


def create_app(settings: Settings, http=httpx) -> FastAPI:
    ref = Reference(settings.data_dir)
    docs = documents_for(ref)
    engine = make_engine(settings, max_connections=4)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_schema(engine)
        seed_claims(engine, ref)
        yield
        engine.dispose()

    app = FastAPI(title="Farting Agent", lifespan=lifespan)

    def one(table, key, value) -> Mapping | None:
        with engine.connect() as conn:
            return conn.execute(select(table).where(key == value)).mappings().first()

    def claim_row(claim_id: str) -> Mapping:
        row = one(claims, claims.c.claim_id, claim_id)
        if row is None:
            raise HTTPException(404, f"unknown claim {claim_id}")
        return row

    def trace_rows(claim_id: str, kind: str | None = None) -> list[Mapping]:
        query = select(trace_events).where(trace_events.c.claim_id == claim_id).order_by(trace_events.c.seq)
        if kind:
            query = query.where(trace_events.c.kind == kind)
        with engine.connect() as conn:
            return conn.execute(query).mappings().all()

    def read_sections(claim_id: str) -> list[dict]:
        """The sections the agent actually read for a claim, in order, each once."""
        read, seen = [], set()
        for r in trace_rows(claim_id, "tool_result"):
            result = json.loads(r["content"])
            match = _SECTION_READ.match(str(result.get("content", ""))) if result.get("name") == "read_section" else None
            if not match:
                continue
            key = (match[1], int(match[2]))
            if key in seen:
                continue
            seen.add(key)
            try:
                read.append({"document": key[0], "section": key[1], "title": docs.section(*key).title})
            except KeyError:
                continue
        return read

    def payment_view(payment: Mapping | None) -> dict | None:
        if payment is None:
            return None
        handler = next((h for h in ref.handlers if h["handler_id"] == payment["approved_by"]), None)
        return {**plain(payment), "approved_by_name": handler["name"] if handler else None}

    def forward_run(path: str, body: dict) -> dict:
        try:
            return agent_client.start_run(http, settings.agent_url, path, body)
        except agent_client.AgentDown:
            raise HTTPException(503, "the agent is not running")
        except agent_client.AgentRefused as exc:
            raise HTTPException(exc.status_code, exc.detail)

    @app.get("/claims", operation_id="list_claims")
    def list_claims():
        """List every claim with its status and decision."""
        query = (
            select(claims.c.claim_id, claims.c.status, claims.c.attempts, claims.c.payload,
                   decisions.c.decision, decisions.c.payout)
            .select_from(claims.outerjoin(decisions, claims.c.claim_id == decisions.c.claim_id))
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

    @app.get("/claims/{claim_id}", operation_id="get_claim")
    def get_claim(claim_id: str):
        """Get one claim with its policy, customer, decision, payment, cost and what the agent read."""
        row = claim_row(claim_id)
        decision = one(decisions, decisions.c.claim_id, claim_id)
        usage = runs.sum_usage([json.loads(u["content"]) for u in trace_rows(claim_id, "usage")])
        claim = json.loads(row["payload"])
        return {
            "claim": claim,
            "status": row["status"],
            "attempts": row["attempts"],
            "last_error": row["last_error"],
            "policy": ref.policy(claim["policy_id"]),
            "customer": ref.customer(claim["customer_id"]),
            "decision": _decision(decision),
            "payment": payment_view(one(payments, payments.c.claim_id, claim_id)),
            "usage": usage,
            "read": read_sections(claim_id),
        }

    @app.get("/claims/{claim_id}/trace", operation_id="get_claim_trace")
    def get_trace(claim_id: str):
        """List every step recorded on a claim, in order."""
        claim_row(claim_id)
        return [{"seq": r["seq"], "kind": r["kind"], "content": json.loads(r["content"]),
                 "created_at": iso(r["created_at"])} for r in trace_rows(claim_id)]

    @app.post("/runs", status_code=202, operation_id="start_run")
    def start_run(body: RunLimit):
        """Ask the agent to work the waiting claims, up to a limit."""
        return forward_run("/runs", {"limit": body.limit})

    @app.post("/runs/selected", status_code=202, operation_id="start_selected_run")
    def start_selected_run(body: SelectedClaims):
        """Ask the agent to work only the given claims."""
        for claim_id in body.claim_ids:
            claim_row(claim_id)
        return forward_run("/runs/selected", {"claim_ids": body.claim_ids})

    def run_view(row: Mapping | None) -> dict:
        return {"run": _run(row), "todo": runs.todo_list(engine, row["run_id"]) if row else []}

    @app.get("/runs/latest", operation_id="get_latest_run")
    def latest_run():
        """Get the latest run and its to-do list."""
        return run_view(runs.latest_run(engine))

    @app.get("/runs/{run_id}", operation_id="get_run")
    def get_run(run_id: int):
        """Get one run with its to-do list and events."""
        row = runs.get_run(engine, run_id)
        if row is None:
            raise HTTPException(404, f"unknown run {run_id}")
        return {**run_view(row), "events": runs.run_events(engine, run_id)}

    @app.get("/instructions", operation_id="get_instructions")
    def instructions():
        """Show the instructions for running the queue and for working a claim."""
        return {"run": load_instructions("run"), "claim": claim_prompt(ref)}

    @app.post("/payouts/calculate", operation_id="calculate_payout")
    def payout(body: PayoutBody):
        """Work out a payout under wording section 10, and say whether it needs a sign-off."""
        return calculate_payout(body.items, body.third_party_paid, body.deductible, body.benefit_max,
                                ref.company["payout_approval_limit"])

    @app.get("/approvals", operation_id="list_approvals")
    def approvals():
        """List the payouts waiting for a handler's sign-off."""
        query = (
            select(payments, claims.c.payload, decisions.c.rationale)
            .select_from(
                payments.join(claims, payments.c.claim_id == claims.c.claim_id)
                .outerjoin(decisions, payments.c.claim_id == decisions.c.claim_id)
            )
            .where(payments.c.status == "held")
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
            })
        return out

    @app.get("/handlers", operation_id="list_handlers")
    def handlers():
        """List the handlers who may sign off a payout over the limit."""
        return [
            {"handler_id": h["handler_id"], "name": h["name"], "role": h["role"]}
            for h in ref.handlers
            if h["can_approve_over_limit"]
        ]

    @app.post("/payments/{payment_id}/approve", operation_id="sign_off_payment")
    def approve(payment_id: str, body: ApproveBody, background: BackgroundTasks):
        """Record a handler's sign-off, then tell the agent, which pays."""
        try:
            payment = sign_off(engine, ref, payment_id, body.handler_id)
        except LookupError:
            raise HTTPException(404, f"unknown payment {payment_id}")
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        # Runs after the response: the backend does not wait for the payment, and the sign-off is already saved.
        background.add_task(agent_client.notify_sign_off, http, settings.agent_url, payment.claim_id)
        return asdict(payment)

    return app
