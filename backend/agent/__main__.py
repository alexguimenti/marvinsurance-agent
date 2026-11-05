"""Work one claim from the command line, in this process: `uv run -m agent CLM-1001`, from the backend folder.

It carries the claim on if it is in progress, or starts it if it is waiting, with no run. Dispatches of the whole
queue start from the dashboard, through the agent's service.
"""
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from agent.service import prepare_database
from agent.worker import open_env, work
from app.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agent", description="Work one claim.")
    parser.add_argument("claim_id", help="the claim to work, for example CLM-1001")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    load_dotenv(ROOT / ".env")  # this entry point only: importing the agent must not read it
    settings = Settings()
    prepare_database(settings)
    with open_env(settings) as env:
        line = work(env, args.claim_id, None)
    print(line)
    return 1 if ": not worked" in line or "failed" in line else 0


if __name__ == "__main__":
    sys.exit(main())
