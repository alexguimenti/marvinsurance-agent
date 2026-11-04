"""Reseed: clear every analysis the agent has made and load the claims, so the queue can be run from scratch.

    npm run reseed                 (on Supabase, when DATABASE_URL is in .env)
    python scripts/reset_db.py     (on whatever this process's environment points at)

The tables are made again, so decisions, activity logs, payments and runs are gone and every claim is
waiting. Run it with no agent working; the backend can stay up.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import get_settings  # noqa: E402
from agent.checkpoints import clear_checkpoints  # noqa: E402
from app.db import make_engine  # noqa: E402
from app.reference import Reference  # noqa: E402
from app.seed import reseed  # noqa: E402


def main() -> None:
    settings = get_settings()
    engine = make_engine(settings, max_connections=2)
    waiting = reseed(engine, Reference(settings.data_dir))
    clear_checkpoints(settings)
    engine.dispose()
    where = f"Supabase ({engine.url.host})" if settings.database_url else settings.db_path
    print(f"Reseeded {where}. {waiting} claims are waiting.")


if __name__ == "__main__":
    main()
