"""What a live run left behind, with no secrets and no claim text.

    python scripts/check_live.py                the claims, runs and connections on the database in .env
    python scripts/check_live.py CLM-1003       also that claim's thread, payment and Langfuse traces
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import text  # noqa: E402

load_dotenv(ROOT / ".env")

from agent.tracing import session_name  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import make_engine  # noqa: E402

CONNECTIONS = "SELECT count(*) FROM pg_stat_activity WHERE usename = 'postgres'"


def langfuse_traces(session: str) -> list:
    from langfuse import Langfuse

    client = Langfuse(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        base_url=os.environ.get("LANGFUSE_BASE_URL"),
    )
    return client.api.trace.list(session_id=session, limit=50).data


def main() -> None:
    engine = make_engine(Settings(), max_connections=1)
    with engine.connect() as conn:
        print("connections for user postgres:", conn.execute(text(CONNECTIONS)).scalar_one())
        print("claims:", dict(conn.execute(text("SELECT status, count(*) FROM claims GROUP BY status")).all()))
        for row in conn.execute(text("SELECT run_id, status, cap, error FROM runs ORDER BY run_id")).all():
            print("run:", tuple(row))
        if len(sys.argv) < 2:
            return
        claim_id = sys.argv[1]
        row = conn.execute(text("SELECT status, attempts, thread_id FROM claims WHERE claim_id = :c"), {"c": claim_id}).one()
        print(f"{claim_id}: status={row[0]} attempts={row[1]} thread={row[2]}")
        print("decisions:", conn.execute(text("SELECT count(*) FROM decisions WHERE claim_id = :c"), {"c": claim_id}).scalar_one())
        payment = conn.execute(
            text("SELECT status, amount, approved_by, approved_at, paid_at FROM payments WHERE claim_id = :c"), {"c": claim_id}
        ).first()
        print("payment:", tuple(payment) if payment else None)
    engine.dispose()
    traces = sorted(langfuse_traces(session_name(claim_id, row[2])), key=lambda t: t.timestamp)
    print(f"Langfuse session {session_name(claim_id, row[2])}: {len(traces)} traces")
    for trace in traces:
        print(" ", trace.timestamp.isoformat(), trace.name)


if __name__ == "__main__":
    main()
