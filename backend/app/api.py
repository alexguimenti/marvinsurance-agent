import json
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

from app.agent import build_agent, load_instructions
from app.config import Settings
from app.db import connect, init_schema
from app.payments import approve_payment
from app.reference import Reference
from app.runner import run_claim, run_queue
from app.seed import seed_claims


class ApproveBody(BaseModel):
    handler_id: str


class RunQueueBody(BaseModel):
    claim_ids: list[str] | None = None


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
        "created_at": row["created_at"],
    }


def create_app(settings: Settings) -> FastAPI:
    ref = Reference(settings.data_dir)

    def db() -> sqlite3.Connection:
        return connect(settings.db_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        conn = db()
        init_schema(conn)
        seed_claims(conn, ref)
        # a crash mid-run leaves claims in_progress for good; start them again, trace and all
        stuck = [r[0] for r in conn.execute("SELECT claim_id FROM claims WHERE status='in_progress'")]
        for claim_id in stuck:
            conn.execute("DELETE FROM trace_events WHERE claim_id = ?", (claim_id,))
            conn.execute("UPDATE claims SET status='waiting' WHERE claim_id = ?", (claim_id,))
        conn.commit()
        conn.close()
        yield

    app = FastAPI(title="Farting Agent", lifespan=lifespan)

    def claim_row(conn: sqlite3.Connection, claim_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"unknown claim {claim_id}")
        return row

    def run_one(claim_id: str) -> None:
        conn = db()
        try:
            run_claim(conn, ref, build_agent(settings, conn, ref), claim_id)
        finally:
            conn.close()

    def run_all(claim_ids: list[str] | None = None) -> None:
        conn = db()
        try:
            run_queue(conn, ref, build_agent(settings, conn, ref), claim_ids)
        finally:
            conn.close()

    @app.get("/claims")
    def list_claims():
        conn = db()
        try:
            rows = conn.execute(
                "SELECT c.claim_id, c.status, c.payload, d.decision, d.payout "
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
            usage = conn.execute(
                "SELECT content FROM trace_events WHERE claim_id = ? AND kind = 'usage' ORDER BY seq DESC LIMIT 1",
                (claim_id,),
            ).fetchone()
        finally:
            conn.close()
        claim = json.loads(row["payload"])
        return {
            "claim": claim,
            "status": row["status"],
            "policy": ref.policy(claim["policy_id"]),
            "customer": ref.customer(claim["customer_id"]),
            "decision": _decision(decision),
            "payment": dict(payment) if payment else None,
            "usage": json.loads(usage["content"]) if usage else None,
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

    @app.post("/claims/{claim_id}/run", status_code=202)
    def run_claim_route(claim_id: str, background: BackgroundTasks):
        conn = db()
        try:
            row = claim_row(conn, claim_id)
        finally:
            conn.close()
        if row["status"] != "waiting":
            raise HTTPException(409, f"claim is {row['status']}, not waiting")
        background.add_task(run_one, claim_id)
        return {"claim_id": claim_id, "accepted": True}

    @app.post("/queue/run", status_code=202)
    def run_queue_route(background: BackgroundTasks, body: RunQueueBody | None = None):
        claim_ids = body.claim_ids if body else None
        conn = db()
        try:
            if claim_ids is not None:
                for claim_id in claim_ids:
                    claim_row(conn, claim_id)
            waiting = [r["claim_id"] for r in conn.execute("SELECT claim_id FROM claims WHERE status='waiting'")]
        finally:
            conn.close()
        queued = waiting if claim_ids is None else [i for i in waiting if i in set(claim_ids)]
        background.add_task(run_all, claim_ids)
        return {"queued": len(queued)}

    @app.get("/instructions")
    def instructions():
        return {"text": load_instructions()}

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
