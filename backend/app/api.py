import json
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from app import runs
from app.agent import load_instructions
from app.config import Settings
from app.db import connect, init_schema
from app.documents import Documents
from app.orchestrator import run_harness
from app.payments import approve_payment
from app.reference import Reference
from app.seed import seed_claims


class ApproveBody(BaseModel):
    handler_id: str


class RunBody(BaseModel):
    claim_ids: list[str] | None = None
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
    docs = Documents(ref)

    def db() -> sqlite3.Connection:
        return connect(settings.db_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        conn = db()
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
        conn = db()
        try:
            run_harness(conn, ref, settings, run_id)
        finally:
            conn.close()

    def read_sections(conn: sqlite3.Connection, claim_id: str) -> list[dict]:
        """The sections the agent read for a claim, in order, each once."""
        rows = conn.execute(
            "SELECT content FROM trace_events WHERE claim_id = ? AND kind = 'tool_call' ORDER BY seq", (claim_id,)
        )
        read, seen = [], set()
        for r in rows:
            call = json.loads(r["content"])
            if call.get("name") != "read_section":
                continue
            args = call.get("args") or {}
            key = (args.get("document"), args.get("section"))
            if key in seen:
                continue
            seen.add(key)
            try:
                title = docs.section(*key).title
            except (KeyError, TypeError):
                continue  # a lookup that failed read nothing
            read.append({"document": key[0], "section": key[1], "title": title})
        return read

    @app.get("/claims")
    def list_claims():
        conn = db()
        try:
            rows = conn.execute(
                "SELECT c.claim_id, c.status, c.attempts, c.payload, d.decision, d.payout "
                "FROM claims c LEFT JOIN decisions d USING (claim_id) ORDER BY c.claim_id"
            ).fetchall()
        finally:
            conn.close()
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
    def get_claim(claim_id: str):
        conn = db()
        try:
            row = claim_row(conn, claim_id)
            decision = conn.execute("SELECT * FROM decisions WHERE claim_id = ?", (claim_id,)).fetchone()
            payment = conn.execute("SELECT * FROM payments WHERE claim_id = ?", (claim_id,)).fetchone()
            usage = runs.sum_usage([json.loads(u["content"]) for u in conn.execute(
                "SELECT content FROM trace_events WHERE claim_id = ? AND kind = 'usage'", (claim_id,))])
            read = read_sections(conn, claim_id)
        finally:
            conn.close()
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
            "read": read,
        }

    @app.get("/claims/{claim_id}/trace")
    def get_trace(claim_id: str):
        conn = db()
        try:
            claim_row(conn, claim_id)
            rows = conn.execute(
                "SELECT seq, kind, content, created_at FROM trace_events WHERE claim_id = ? ORDER BY seq",
                (claim_id,),
            ).fetchall()
        finally:
            conn.close()
        return [{"seq": r["seq"], "kind": r["kind"], "content": json.loads(r["content"]),
                 "created_at": r["created_at"]} for r in rows]

    @app.post("/runs", status_code=202)
    def start_run(background: BackgroundTasks, body: RunBody | None = None):
        body = body or RunBody()
        model = "scripted" if settings.agent_provider == "scripted" else settings.agent_model
        conn = db()
        try:
            for claim_id in body.claim_ids or []:
                claim_row(conn, claim_id)
            try:
                run_id = runs.start_run(conn, model, body.limit, body.claim_ids)
            except runs.RunActive as exc:
                raise HTTPException(409, str(exc))
        finally:
            conn.close()
        background.add_task(work_run, run_id)
        return {"run_id": run_id}

    def run_view(conn: sqlite3.Connection, row: sqlite3.Row | None) -> dict:
        return {"run": _run(row), "todo": runs.todo_list(conn, row["run_id"]) if row else []}

    @app.get("/runs/latest")
    def latest_run():
        conn = db()
        try:
            return run_view(conn, conn.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT 1").fetchone())
        finally:
            conn.close()

    @app.get("/runs/{run_id}")
    def get_run(run_id: int):
        conn = db()
        try:
            row = runs.get_run(conn, run_id)
            if row is None:
                raise HTTPException(404, f"unknown run {run_id}")
            return {**run_view(conn, row), "events": runs.run_events(conn, run_id)}
        finally:
            conn.close()

    @app.get("/instructions")
    def instructions():
        return {"run": load_instructions("run"), "claim": load_instructions("claim")}

    @app.get("/approvals")
    def approvals():
        conn = db()
        try:
            rows = conn.execute(
                "SELECT p.payment_id, p.claim_id, p.amount, p.status, p.created_at, c.payload, d.rationale "
                "FROM payments p JOIN claims c USING (claim_id) LEFT JOIN decisions d USING (claim_id) "
                "WHERE p.status = 'held' ORDER BY p.claim_id"
            ).fetchall()
        finally:
            conn.close()
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
    def approve(payment_id: str, body: ApproveBody):
        conn = db()
        try:
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
        finally:
            conn.close()

    return app
