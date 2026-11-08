"""The platform's settings, kept in the database: the approval limit, the business objectives, the noise and the
tolerance, the spending limit for a round and how many claim agents the improver's evaluations use. Every change
records who made it and when. Seeded from data/company.json (the approval limit) and data/improvement.json."""
import json
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, insert, select, update

from app.auth import Handler
from app.config import Settings
from app.db import insert_or_ignore, iso, settings_history, settings_table
from app.reference import Reference

MEASURES = ("accuracy", "grade", "cost_per_claim", "seconds_per_claim")
MAX_AGENTS_AT_ONCE = 25
SETTING_NAMES = ("approval_limit", "objectives", "noise", "tolerance", "round_spending_limit", "agents_at_once")


def _number(value: Any, name: str, *, above: float | None = None, at_least: float | None = None, at_most: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if above is not None and not value > above:
        raise ValueError(f"{name} must be above {above}")
    if at_least is not None and value < at_least:
        raise ValueError(f"{name} must be at least {at_least}")
    if at_most is not None and value > at_most:
        raise ValueError(f"{name} must be at most {at_most}")
    return value


def _per_measure(value: Any, name: str, keys: tuple[str, ...], limits: dict[str, float]) -> dict:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{name} needs exactly these: {', '.join(keys)}")
    return {key: _number(value[key], f"{name} {key}", at_least=0, at_most=limits.get(key)) for key in keys}


def validate(name: str, value: Any) -> Any:
    """The value to store, or a ValueError saying what is wrong with it."""
    if name == "approval_limit":
        return _number(value, "the approval limit", above=0)
    if name == "round_spending_limit":
        return _number(value, "the spending limit", above=0)
    if name == "agents_at_once":
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_AGENTS_AT_ONCE:
            raise ValueError(f"claim agents at once must be a whole number from 1 to {MAX_AGENTS_AT_ONCE}")
        return value
    if name == "objectives":
        return _per_measure(value, "objectives", ("accuracy", "grade"), {"accuracy": 1.0, "grade": 3.0})
    if name in ("noise", "tolerance"):
        return _per_measure(value, name, MEASURES, {"accuracy": 1.0})
    raise LookupError(f"no setting called {name}")


def seed_settings(engine: Engine, ref: Reference) -> None:
    """Fill what is missing and leave every setting a handler has changed."""
    improvement = ref.read("improvement.json")
    values = {"approval_limit": ref.company["payout_approval_limit"], **improvement}
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(insert_or_ignore(conn, settings_table).values(
            [{"name": n, "value": json.dumps(v), "changed_by": "seed", "changed_at": now} for n, v in values.items()]
        ))


def get_settings(engine: Engine) -> dict[str, Any]:
    with engine.connect() as conn:
        rows = conn.execute(select(settings_table.c.name, settings_table.c.value)).all()
    return {name: json.loads(value) for name, value in rows}


def approval_limit(engine: Engine, ref: Reference) -> float:
    """The limit in force now. An older database with no settings row falls back to the company file."""
    with engine.connect() as conn:
        value = conn.execute(select(settings_table.c.value).where(settings_table.c.name == "approval_limit")).scalar_one_or_none()
    return json.loads(value) if value is not None else ref.company["payout_approval_limit"]


def change_setting(engine: Engine, name: str, value: Any, handler: Handler) -> dict:
    """Change one setting and record who did it and when. Raises ValueError for a value that makes no sense and
    LookupError for a setting that does not exist."""
    stored = validate(name, value)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        changed = conn.execute(
            update(settings_table).where(settings_table.c.name == name)
            .values(value=json.dumps(stored), changed_by=handler.handler_id, changed_at=now)
        ).rowcount
        if changed == 0:
            raise LookupError(f"no setting called {name}")
        conn.execute(insert(settings_history).values(name=name, value=json.dumps(stored), changed_by=handler.handler_id, changed_at=now))
    return {"name": name, "value": stored, "changed_by": handler.handler_id, "changed_at": iso(now)}


def setting_history(engine: Engine, current_only: bool = False) -> list[dict]:
    """Every change, newest first; or, with current_only, each setting as it stands with who set it."""
    table = settings_table if current_only else settings_history
    with engine.connect() as conn:
        rows = conn.execute(select(table).order_by(table.c.changed_at.desc(), table.c.name)).mappings().all()
    return [{"name": r["name"], "value": json.loads(r["value"]), "changed_by": r["changed_by"], "changed_at": iso(r["changed_at"])} for r in rows]


def models_allowed(ref: Reference, settings: Settings) -> dict:
    """The models the claim agent may use, with their reasoning efforts and prices in dollars per million tokens.
    With MODEL_NAME set it is that model alone, at effort `default` and free: the course's free models."""
    if os.environ.get("MODEL_NAME"):
        return {settings.agent_model: {"reasoning_efforts": ["default"], "price_in": 0.0, "price_out": 0.0}}
    return ref.read("models.json")
