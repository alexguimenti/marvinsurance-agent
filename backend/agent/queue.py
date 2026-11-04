"""The agent that runs the queue: it plans a to-do list and hands each claim to a fresh claim agent.
It stays on create_agent; the claim agent is the one that waits and carries on."""
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Protocol

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel
from sqlalchemy import select

from agent.events import Emit, Usage, emit_message
from agent.prompts import load_instructions
from agent.worker import AgentEnv, carry_on, stopped_claims
from agent.worker import work_claim as work_one_claim
from app import runs
from app.db import claims as claims_table

log = logging.getLogger(__name__)

WORK_THE_QUEUE = "Work the claims queue."
FINISH_THE_QUEUE = ("Work the claims queue. This run was stopped earlier and you are finishing it: "
                    "some claims are already done, and the to-do list keeps them.")
ADOPTED_NOTE = "This run was stopped earlier and has now been finished, with its own limit and claims."
HEARTBEAT_S = 15


class TodoInput(BaseModel):
    title: str
    claim_id: str | None = None


def build_run_tools(env: AgentEnv, run_id: int) -> list[BaseTool]:
    """The queue agent's tools, bound to one run. Code, not the model, enforces scope, cap and attempts."""
    engine = env.engine
    max_attempts = env.settings.agent_max_attempts

    @tool
    def list_waiting_claims() -> str:
        """List the waiting claims this run may work, with the run's cap and how many claims it has taken."""
        cap = runs.get_run(engine, run_id)["cap"]
        claims = []
        for claim_id in runs.in_scope_waiting(engine, run_id):
            with engine.connect() as conn:
                row = conn.execute(
                    select(claims_table.c.payload, claims_table.c.attempts).where(claims_table.c.claim_id == claim_id)
                ).mappings().one()
            claim = json.loads(row["payload"])
            claims.append({"claim_id": claim_id, "claim_type": claim["claim_type"], "summary": claim["summary"],
                           "amount_claimed": claim["amount_claimed"], "attempts": row["attempts"]})
        return json.dumps({"cap": cap, "taken": len(runs.taken_claims(engine, run_id)), "claims": claims})

    @tool
    def set_todo(items: list[TodoInput]) -> str:
        """Write the plan as a to-do list, replacing any earlier one. One item per claim, plus any other step.
        Items already done stay done."""
        planned = [i.model_dump() if isinstance(i, BaseModel) else dict(i) for i in items]
        return json.dumps(runs.set_todo(engine, run_id, planned))

    @tool
    def update_todo(item_id: int, status: str, note: str = "") -> str:
        """Change one item of the to-do list: status is todo, in_progress, done or left_for_handler."""
        try:
            runs.update_todo(engine, run_id, item_id, status, note or None)
        except ValueError as exc:
            return f"Error: {exc}"
        return json.dumps(runs.todo_list(engine, run_id))

    @tool
    def work_claim(claim_id: str) -> str:
        """Hand one claim to a claim agent that works it from a clean start. Returns the outcome in one line."""
        if not runs.in_scope(engine, run_id, claim_id):
            return f"{claim_id}: not worked, it is not part of this run."
        taken = {r["claim_id"] for r in runs.taken_claims(engine, run_id)}
        if claim_id not in taken and runs.cap_reached(engine, run_id):
            cap = runs.get_run(engine, run_id)["cap"]
            return f"{claim_id}: not worked, the run's cap of {cap} claims is reached."
        return work_one_claim(env, claim_id, run_id, max_attempts)

    @tool
    def finish_run(summary: str) -> str:
        """Finish the run with a short summary for the handlers. Refused while claims are still owed."""
        missed = runs.missed_claims(engine, run_id)
        if missed:
            return f"Not finished. Still to work: {', '.join(missed)}"
        runs.set_summary(engine, run_id, summary)
        return "Run finished."

    return [list_waiting_claims, set_todo, update_todo, work_claim, finish_run]


class QueueAgent(Protocol):
    model_id: str
    usage: dict | None

    def run(self, tools: list[BaseTool], on_event: Emit, resuming: bool = False) -> None: ...


class LangChainQueueAgent:
    def __init__(self, model, model_id: str, max_steps: int = 400):
        self.model, self.model_id, self.max_steps = model, model_id, max_steps
        self.usage: dict | None = None

    def run(self, tools: list[BaseTool], on_event: Emit, resuming: bool = False) -> None:
        agent = create_agent(self.model, tools, system_prompt=load_instructions("run"))
        total = Usage()
        calls: dict[str, dict] = {}
        request = {"messages": [HumanMessage(FINISH_THE_QUEUE if resuming else WORK_THE_QUEUE)]}
        for chunk in agent.stream(request, stream_mode="updates", config={"recursion_limit": self.max_steps}):
            for update in chunk.values():
                for message in (update or {}).get("messages", []):
                    if isinstance(message, AIMessage):
                        total.add(message)
                        self.usage = total.as_dict()  # kept up to date, so a failed run still reports its cost
                    emit_message(message, on_event, calls)


def build_queue_agent(env: AgentEnv) -> QueueAgent:
    if env.settings.agent_provider == "scripted":
        from agent.scripted import ScriptedQueueAgent  # stand-in for tests and e2e runs

        return ScriptedQueueAgent()
    from agent.models import chat_model

    return LangChainQueueAgent(chat_model(env.settings), env.settings.agent_model, env.settings.agent_run_max_steps)


def run_harness(env: AgentEnv, run_id: int, queue_agent: QueueAgent | None = None, resuming: bool = False) -> None:
    """Run the queue agent for one run, then close the run. Never raises on an ordinary error."""
    engine = env.engine
    error = None
    try:
        queue_agent = queue_agent or build_queue_agent(env)
        tools = build_run_tools(env, run_id)
        queue_agent.run(tools, lambda kind, content: runs.run_event(engine, run_id, kind, content), resuming)
    except Exception as exc:  # the run ends as failed; claims already decided stay decided
        error = f"{type(exc).__name__}: {exc}"
    usage = getattr(queue_agent, "usage", None)
    if usage:
        runs.run_event(engine, run_id, "usage", usage)

    missed = runs.missed_claims(engine, run_id)
    finished = runs.get_run(engine, run_id)["summary"] is not None
    if error is None and finished and not missed:
        runs.finish(engine, run_id, "finished")
        return
    if error is None:
        error = "the agent stopped without finishing the run"
    if missed:
        error += f"; missed: {', '.join(missed)}"
    runs.finish(engine, run_id, "failed", error=error)


def begin_run(env: AgentEnv, limit: int | None, claim_ids: list[str] | None) -> tuple[int, bool]:
    """Start a run, or adopt one that was stopped. Returns its id and whether it was adopted. A stopped run is
    finished with its own cap and scope; the new request's are dropped. Raises RunActive for a run still alive."""
    now = datetime.now(timezone.utc)
    if runs.live_run(env.engine, now) is not None:
        raise runs.RunActive("a run is already in progress")
    stale = runs.stale_run(env.engine, now)
    if stale is not None:
        runs.touch(env.engine, stale["run_id"])
        runs.run_event(env.engine, stale["run_id"], "adopted", {"note": ADOPTED_NOTE})
        return stale["run_id"], True
    return runs.start_run(env.engine, env.model_id, limit, claim_ids), False


def carry_on_everything(env: AgentEnv) -> list[str]:
    """Pick up every claim that was stopped or signed off while the agent was away."""
    return [carry_on(env, claim_id) for claim_id in stopped_claims(env.engine)]


def work_run(env: AgentEnv, run_id: int, adopted: bool = False, queue_agent: QueueAgent | None = None) -> None:
    """A whole run: carry on what is unfinished, then work the queue. A heartbeat shows that this process is alive."""
    done = threading.Event()

    def beat() -> None:
        while not done.wait(HEARTBEAT_S):
            try:
                runs.touch(env.engine, run_id)
            except Exception as exc:  # a missed beat is not worth stopping the run for
                log.warning("could not record the run's heartbeat: %s", exc)

    threading.Thread(target=beat, daemon=True).start()
    closed = False
    try:
        carry_on_everything(env)
        run_harness(env, run_id, queue_agent, resuming=adopted)
        if adopted:
            runs.note_on_summary(env.engine, run_id, ADOPTED_NOTE)
        closed = True
    finally:
        done.set()
        if not closed:  # Ctrl-C or the like: the next run need not wait for the heartbeat to go stale
            runs.release(env.engine, run_id)
