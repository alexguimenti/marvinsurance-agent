"""Clear every analysis the agent has made, so the queue can be run again from scratch.

    python scripts/reset_db.py

Safe to run while the backend is up: claims go back to "waiting", and decisions, activity
logs and payments are deleted. The claims themselves are kept.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import get_settings  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.db import claims, decisions, init_schema, make_engine, reset_analyses  # noqa: E402


def main() -> None:
    settings = get_settings()
    engine = make_engine(settings, max_connections=2)
    init_schema(engine)
    with engine.connect() as conn:
        done = conn.execute(select(func.count()).select_from(decisions)).scalar_one()
    reset_analyses(engine)
    with engine.connect() as conn:
        waiting = conn.execute(select(func.count()).select_from(claims).where(claims.c.status == "waiting")).scalar_one()
    engine.dispose()
    print(f"Cleared {done} analyses in {settings.db_path}. {waiting} claims are waiting.")


if __name__ == "__main__":
    main()
