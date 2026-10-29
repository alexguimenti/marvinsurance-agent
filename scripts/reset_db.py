"""Clear every analysis the agent has made, so the queue can be run again from scratch.

    python scripts/reset_db.py

Safe to run while the backend is up: claims go back to "waiting", and decisions, activity
logs and payments are deleted. The claims themselves are kept.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import connect, init_schema, reset_analyses  # noqa: E402


def main() -> None:
    settings = get_settings()
    conn = connect(settings.db_path)
    init_schema(conn)
    done = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    reset_analyses(conn)
    waiting = conn.execute("SELECT COUNT(*) FROM claims WHERE status = 'waiting'").fetchone()[0]
    conn.close()
    print(f"Cleared {done} analyses in {settings.db_path}. {waiting} claims are waiting.")


if __name__ == "__main__":
    main()
