import json
import re
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Iterator

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from app import runs
from app.agent import claim_prompt, load_instructions
from app.config import Settings
from app.db import connect, init_schema
from app.documents import documents_for
from app.orchestrator import run_harness
from app.payments import approve_payment
from app.payout import LineItem, calculate_payout
from app.reference import Reference
from app.seed import seed_claims

# How LangChainAgent traces a section it read: "[claims_manual section 7: Interpretation notes, 5000 characters]".
# Refused reads (errors, rereads, past the cap) are traced as plain text and do not match.
_SECTION_READ = re.compile(r"^\[(\w+) section (\d+): .+, \d+ characters\]$")


class ApproveBody(BaseModel):
    handler_id: str


class PayoutBody(BaseModel):
    items: list[LineItem]
    third_party_paid: float
    deductible: float
    benefit_max: float | None


class RunBody(BaseModel):
    claim_ids: list[str] | None = Field(default=None, min_length=1)
    limit: int | None = Field(default=None, ge=1)


def _decision(row: sqlite3.Row | None) -> dict | None:
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
        "created_at": row["created_at"],
    }


def _run(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    out = {k: row[k] for k in ("run_id", "status", "cap", "model", "summary", "error", "started_at", "finished_at")}
    out["usage"] = json.loads(row["usage"]) if row["usage"] else None
    return out


def create_app(settings: Settings) -> FastAPI:
    ref = Reference(settings.data_dir)
    docs = documents_for(ref)

    def db() -> Iterator[sqlite3.Connection]:
        """One connection per request, closed when the request ends."""
        conn = connect(settings.db_path)
        try:
            yield conn
        finally:
            conn.close()

    Conn = Annotated[sqlite3.Connection, Depends(db)]

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        conn = connect(settings.db_path)
        init_schema(conn)
        seed_claims(conn, ref)
        runs.recover_after_restart(conn)  # a server stopped mid-run leaves the run and its claims hanging
        conn.close()
        yield

    app = FastAPI(title="Farting Agent", lifespan=lifespan)

    def claim_row(conn: sqlite3.Connection, claim_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"unknown claim {claim_id}")
        return row

    def work_run(run_id: int) -> None:
        conn = connect(settings.db_path)  # a background task outlives the request, so it has its own connection
        try:
            run_harness(conn, ref, settings, run_id)
        finally:
            conn.close()

    def read_sections(conn: sqlite3.Connection, claim_id: str) -> list[dict]:
        """The sections the agent actually read for a claim, in order, each once."""
        rows = conn.execute(
            "SELECT content FROM trace_events WHERE claim_id = ? AND kind = 'tool_result' ORDER BY seq", (claim_id,)
        )
        read, seen = [], set()
        for r in rows:
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

    @app.get("/claims")
    def list_claims(conn: Conn):
        rows = conn.execute(
            "SELECT c.claim_id, c.status, c.attempts, c.payload, d.decision, d.payout "
            "FROM claims c LEFT JOIN decisions d USING (claim_id) ORDER BY c.claim_id"
        ).fetchall()
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

    @app.get("/claims/{claim_id}")
    def get_claim(claim_id: str, conn: Conn):
        row = claim_row(conn, claim_id)
        decision = conn.execute("SELECT * FROM decisions WHERE claim_id = ?", (claim_id,)).fetchone()
        payment = conn.execute("SELECT * FROM payments WHERE claim_id = ?", (claim_id,)).fetchone()
        usage = runs.sum_usage([json.loads(u["content"]) for u in conn.execute(
            "SELECT content FROM trace_events WHERE claim_id = ? AND kind = 'usage'", (claim_id,))])
        claim = json.loads(row["payload"])
        return {
            "claim": claim,
            "status": row["status"],
            "attempts": row["attempts"],
            "last_error": row["last_error"],
            "policy": ref.policy(claim["policy_id"]),
            "customer": ref.customer(claim["customer_id"]),
            "decision": _decision(decision),
            "payment": dict(payment) if payment else None,
            "usage": usage,
            "read": read_sections(conn, claim_id),
        }

    @app.get("/claims/{claim_id}/trace")
    def get_trace(claim_id: str, conn: Conn):
        claim_row(conn, claim_id)
        rows = conn.execute(
            "SELECT seq, kind, content, created_at FROM trace_events WHERE claim_id = ? ORDER BY seq", (claim_id,)
        ).fetchall()
        return [{"seq": r["seq"], "kind": r["kind"], "content": json.loads(r["content"]),
                 "created_at": r["created_at"]} for r in rows]

    @app.post("/runs", status_code=202)
    def start_run(background: BackgroundTasks, conn: Conn, body: RunBody | None = None):
        body = body or RunBody()
        model = "scripted" if settings.agent_provider == "scripted" else settings.agent_model
        for claim_id in body.claim_ids or []:
            claim_row(conn, claim_id)
        try:
            run_id = runs.start_run(conn, model, body.limit, body.claim_ids)
        except runs.RunActive as exc:
            raise HTTPException(409, str(exc))
        background.add_task(work_run, run_id)
        return {"run_id": run_id}

    def run_view(conn: sqlite3.Connection, row: sqlite3.Row | None) -> dict:
        return {"run": _run(row), "todo": runs.todo_list(conn, row["run_id"]) if row else []}

    @app.get("/runs/latest")
    def latest_run(conn: Conn):
        return run_view(conn, conn.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT 1").fetchone())

    @app.get("/runs/{run_id}")
    def get_run(run_id: int, conn: Conn):
        row = runs.get_run(conn, run_id)
        if row is None:
            raise HTTPException(404, f"unknown run {run_id}")
        return {**run_view(conn, row), "events": runs.run_events(conn, run_id)}

    @app.get("/instructions")
    def instructions():
        return {"run": load_instructions("run"), "claim": claim_prompt(ref)}

    @app.post("/payouts/calculate", operation_id="calculate_payout")
    def payout(body: PayoutBody):
        """Work out a payout under wording section 10, and say whether it needs a sign-off."""
        return calculate_payout(body.items, body.third_party_paid, body.deductible, body.benefit_max,
                                ref.company["payout_approval_limit"])

    @app.get("/approvals")
    def approvals(conn: Conn):
        rows = conn.execute(
            "SELECT p.payment_id, p.claim_id, p.amount, p.status, p.created_at, c.payload, d.rationale "
            "FROM payments p JOIN claims c USING (claim_id) LEFT JOIN decisions d USING (claim_id) "
            "WHERE p.status = 'held' ORDER BY p.claim_id"
        ).fetchall()
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

    @app.get("/handlers")
    def handlers():
        return [
            {"handler_id": h["handler_id"], "name": h["name"], "role": h["role"]}
            for h in ref.handlers
            if h["can_approve_over_limit"]
        ]

    @app.post("/payments/{payment_id}/approve")
    def approve(payment_id: str, body: ApproveBody, conn: Conn):
        try:
            payment = approve_payment(conn, payment_id, body.handler_id, ref)
        except LookupError:
            raise HTTPException(404, f"unknown payment {payment_id}")
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        if payment.status == "paid":
            conn.execute(
                "UPDATE claims SET status='decided' WHERE claim_id = ? AND status='awaiting_approval'",
                (payment.claim_id,),
            )
            conn.commit()
        return asdict(payment)

    return app
