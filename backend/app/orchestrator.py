"""The agent that runs the queue: it plans a to-do list and hands each claim to a fresh claim agent."""
import json
import sqlite3
from typing import Protocol

from langchain.agents import create_agent
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel

from app import runs
from app.agent import Agent, Emit, build_agent, chat_model, load_instructions, run_graph
from app.config import Settings
from app.reference import Reference
from app.runner import work_claim as work_one_claim


class TodoInput(BaseModel):
    title: str
    claim_id: str | None = None


def build_run_tools(conn: sqlite3.Connection, ref: Reference, agent: Agent, run_id: int,
                    max_attempts: int) -> list[BaseTool]:
    """The orchestrator's tools, bound to one run. Code, not the model, enforces scope, cap and attempts."""

    @tool
    def list_waiting_claims() -> str:
        """List the waiting claims this run may work, with the run's cap and how many claims it has taken."""
        cap = runs.get_run(conn, run_id)["cap"]
        claims = []
        for claim_id in runs.in_scope_waiting(conn, run_id):
            row = conn.execute("SELECT payload, attempts FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
            claim = json.loads(row["payload"])
            claims.append({"claim_id": claim_id, "claim_type": claim["claim_type"], "summary": claim["summary"],
                           "amount_claimed": claim["amount_claimed"], "attempts": row["attempts"]})
        return json.dumps({"cap": cap, "taken": len(runs.taken_claims(conn, run_id)), "claims": claims})

    @tool
    def set_todo(items: list[TodoInput]) -> str:
        """Write the plan as a to-do list, replacing any earlier one. One item per claim, plus any other step."""
        planned = [i.model_dump() if isinstance(i, BaseModel) else dict(i) for i in items]
        return json.dumps(runs.set_todo(conn, run_id, planned))

    @tool
    def update_todo(item_id: int, status: str, note: str = "") -> str:
        """Change one item of the to-do list: status is todo, in_progress, done or left_for_handler."""
        try:
            runs.update_todo(conn, run_id, item_id, status, note or None)
        except ValueError as exc:
            return f"Error: {exc}"
        return json.dumps(runs.todo_list(conn, run_id))

    @tool
    def work_claim(claim_id: str) -> str:
        """Hand one claim to a claim agent that works it from a clean start. Returns the outcome in one line."""
        if not runs.in_scope(conn, run_id, claim_id):
            return f"{claim_id}: not worked, it is not part of this run."
        taken = {r["claim_id"] for r in runs.taken_claims(conn, run_id)}
        if claim_id not in taken and runs.cap_reached(conn, run_id):
            cap = runs.get_run(conn, run_id)["cap"]
            return f"{claim_id}: not worked, the run's cap of {cap} claims is reached."
        return work_one_claim(conn, ref, agent, claim_id, run_id, max_attempts)

    @tool
    def finish_run(summary: str) -> str:
        """Finish the run with a short summary for the handlers. Refused while claims are still owed."""
        missed = runs.missed_claims(conn, run_id)
        if missed:
            return f"Not finished. Still to work: {', '.join(missed)}"
        conn.execute("UPDATE runs SET summary = ? WHERE run_id = ?", (summary, run_id))
        conn.commit()
        return "Run finished."

    return [list_waiting_claims, set_todo, update_todo, work_claim, finish_run]


class Orchestrator(Protocol):
    model_id: str
    usage: dict | None

    def run(self, tools: list[BaseTool], on_event: Emit) -> None: ...


class LangChainOrchestrator:
    def __init__(self, model, model_id: str, max_steps: int = 400):
        self.model, self.model_id, self.max_steps = model, model_id, max_steps
        self.usage: dict | None = None

    def run(self, tools: list[BaseTool], on_event: Emit) -> None:
        agent = create_agent(self.model, tools, system_prompt=load_instructions("run"))

        def keep_usage(usage: dict) -> None:
            self.usage = usage

        run_graph(agent, "Work the claims queue.", self.max_steps, on_event, keep_usage)


def build_orchestrator(settings: Settings) -> Orchestrator:
    if settings.agent_provider == "scripted":
        from app.scripted import ScriptedOrchestrator  # stand-in for tests and e2e runs

        return ScriptedOrchestrator()
    return LangChainOrchestrator(chat_model(settings), settings.agent_model, settings.agent_run_max_steps)


def run_harness(conn: sqlite3.Connection, ref: Reference, settings: Settings, run_id: int,
                orchestrator: Orchestrator | None = None, agent: Agent | None = None) -> None:
    """Run the orchestrator for one run, then close the run. Never raises and never leaves a claim in progress."""
    error = None
    try:
        agent = agent or build_agent(settings, conn, ref)
        orchestrator = orchestrator or build_orchestrator(settings)
        tools = build_run_tools(conn, ref, agent, run_id, settings.agent_max_attempts)
        orchestrator.run(tools, lambda kind, content: runs.run_event(conn, run_id, kind, content))
    except Exception as exc:  # the run ends as failed; claims already decided stay decided
        error = f"{type(exc).__name__}: {exc}"
    usage = getattr(orchestrator, "usage", None)
    if usage:
        runs.run_event(conn, run_id, "usage", usage)

    missed = runs.missed_claims(conn, run_id)
    finished = runs.get_run(conn, run_id)["summary"] is not None
    if error is None and finished and not missed:
        runs.finish(conn, run_id, "finished")
        return
    if error is None:
        error = "the agent stopped without finishing the run"
    if missed:
        error += f"; missed: {', '.join(missed)}"
    runs.finish(conn, run_id, "failed", error=error)
