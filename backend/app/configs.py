"""The claim agent's configuration (its instructions, model and reasoning effort) and the improver's recipe, each kept
in versions. Version 1 is what seeding writes. A new version records what changed, why, who proposed it and who approved
it, and is not the current version until a round keeps it. The claim agent's own view of a version holds no scores."""
import difflib
import os
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import Engine, and_, func, insert, select, update

from agent.prompts import load_instructions, load_recipe
from app.config import DEFAULT_EFFORT, DEFAULT_MODEL, _agent_toml
from app.db import config_versions, current_version, insert_or_ignore, iso, recipe_versions, runs
from app.reference import Reference

Kind = Literal["config", "recipe"]
TABLES = {"config": config_versions, "recipe": recipe_versions}


def default_model_and_effort() -> tuple[str, str]:
    """What version 1 uses: MODEL_NAME at effort `default`, or the configured model (the default one runs at low)."""
    name = os.environ.get("MODEL_NAME")
    if name:
        return name, "default"
    model = _agent_toml().get("agent_model") or DEFAULT_MODEL
    return model, DEFAULT_EFFORT if model == DEFAULT_MODEL else "default"


def seed_versions(engine: Engine, ref: Reference) -> None:
    """Make version 1 of each, and make it current. Versions already there, and which one is current, are left alone."""
    model, effort = default_model_and_effort()
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(insert_or_ignore(conn, config_versions).values(
            version=1, instructions=load_instructions("claim"), model=model, reasoning_effort=effort,
            summary="The claim agent as it was before versions", reason="The starting point",
            proposed_by="seed", approved_by="seed", approved_at=now))
        conn.execute(insert_or_ignore(conn, recipe_versions).values(
            version=1, recipe=load_recipe(), summary="The lab's first recipe", reason="The starting point",
            proposed_by="seed", approved_by="seed", approved_at=now))
        conn.execute(insert_or_ignore(conn, current_version).values([{"kind": "config", "version": 1}, {"kind": "recipe", "version": 1}]))


def _row(row) -> dict:
    out = dict(row)
    out["approved_at"] = iso(out.get("approved_at"))
    out["created_at"] = iso(out.get("created_at"))
    return out


def get_version(engine: Engine, kind: Kind, version: int) -> dict:
    table = TABLES[kind]
    with engine.connect() as conn:
        row = conn.execute(select(table).where(table.c.version == version)).mappings().first()
    if row is None:
        raise LookupError(f"there is no {kind} version {version}")
    return _row(row)


def current(engine: Engine, kind: Kind) -> dict:
    with engine.connect() as conn:
        number = conn.execute(select(current_version.c.version).where(current_version.c.kind == kind)).scalar_one_or_none()
    return get_version(engine, kind, number if number is not None else 1)


def list_versions(engine: Engine, kind: Kind) -> list[dict]:
    table = TABLES[kind]
    with engine.connect() as conn:
        return [_row(r) for r in conn.execute(select(table).order_by(table.c.version)).mappings()]


def make_current(engine: Engine, kind: Kind, version: int) -> None:
    get_version(engine, kind, version)  # it must exist
    with engine.begin() as conn:
        conn.execute(update(current_version).where(current_version.c.kind == kind).values(version=version))


def diff(old: str, new: str) -> list[dict]:
    """The lines of two texts, each marked same, add or remove, as the lab's difflib diff."""
    kinds = {"  ": "same", "- ": "remove", "+ ": "add"}
    return [{"kind": kinds[line[:2]], "text": line[2:]} for line in difflib.ndiff(old.splitlines(), new.splitlines()) if line[:2] in kinds]


def _lines_changed(old: str, new: str) -> str:
    lines = diff(old, new)
    added, removed = sum(1 for l in lines if l["kind"] == "add"), sum(1 for l in lines if l["kind"] == "remove")
    return f"{added} line{'s' if added != 1 else ''} added, {removed} removed"


def summarize(kind: Kind, before: dict, after: dict) -> str:
    """What a version changed from the one before, in a line."""
    text = "instructions" if kind == "config" else "recipe"
    parts = []
    if before[text] != after[text]:
        parts.append(_lines_changed(before[text], after[text]))
    if kind == "config":
        if before["model"] != after["model"]:
            parts.append(f"model {before['model']} -> {after['model']}")
        if before["reasoning_effort"] != after["reasoning_effort"]:
            parts.append(f"reasoning effort {before['reasoning_effort']} -> {after['reasoning_effort']}")
    return "; ".join(parts) or "no change"


def add_version(engine: Engine, kind: Kind, fields: dict, approved_by: str, round_id: int | None) -> int:
    """Save an approved proposal as the next version. It is not current until a round keeps it."""
    table = TABLES[kind]
    with engine.connect() as conn:
        latest = conn.execute(select(func.max(table.c.version))).scalar_one()
    before = get_version(engine, kind, latest)
    summary = summarize(kind, before, fields)
    with engine.begin() as conn:
        return conn.execute(insert(table).values(
            version=latest + 1, summary=summary, approved_by=approved_by, approved_at=datetime.now(timezone.utc),
            round_id=round_id, **fields).returning(table.c.version)).scalar_one()


def config_for_run(engine: Engine, run_id: int | None) -> dict:
    """What a claim agent needs from its run's version, and nothing else: no scores, no handler decisions. One
    statement: a trip to the database costs time a sign-off is waiting through."""
    columns = (config_versions.c.version, config_versions.c.instructions, config_versions.c.model, config_versions.c.reasoning_effort)
    of_the_run = select(*columns).select_from(runs.join(config_versions, runs.c.config_version == config_versions.c.version)).where(runs.c.run_id == run_id)
    the_current = select(*columns).select_from(config_versions.join(
        current_version, and_(current_version.c.kind == "config", current_version.c.version == config_versions.c.version)))
    with engine.connect() as conn:
        row = conn.execute(of_the_run).mappings().first() if run_id is not None else None
        if row is None:  # outside a run, or a run with no version recorded
            row = conn.execute(the_current).mappings().first()
    if row is None:
        raise LookupError("there is no configuration version")
    return dict(row)
