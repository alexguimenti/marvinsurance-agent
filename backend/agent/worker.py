"""Working one claim: for the first time, carrying on after a stop or a sign-off, or starting afresh after an error."""
import contextvars
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterator

from sqlalchemy import Engine, select, update

from agent.checkpoints import Checkpoints, make_checkpoints
from agent.graph import build_claim_graph
from agent.tracing import flush_traces, trace_config, warm_up_tracing
from app.claims import get_claim, needs_sign_off
from app.config import Settings
from app.db import claims, make_engine, payments, warm_up
from app.payments import get_payment
from app.reference import Reference
from app.runs import add_trace_event, todo_for_claim
from app.usage import record_work_time

RECORD_CONNECTIONS = 2  # with the checkpointer's 2, the 4 a process may use


@dataclass
class AgentEnv:
    engine: Engine
    ref: Reference
    settings: Settings
    checkpoints: Checkpoints
    model: object
    model_id: str
    clock: Callable[[], float] = time.monotonic  # a test can move it on without sleeping
    _working: set = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _reader: object = None
    _stack: ExitStack = field(default_factory=ExitStack)

    def _build(self, saver):
        return build_claim_graph(self.engine, self.ref, self.model, self.model_id, self.settings.agent_max_model_calls, saver)

    @contextmanager
    def claim_graph(self) -> Iterator:
        """The claim graph on a saver of its own, for one claim run: a saver takes turns between its callers."""
        with self.checkpoints.saver() as saver:
            yield self._build(saver)

    @property
    def graph(self):
        """A graph on a saver of its own for looking at threads from outside a claim run, as tests do."""
        if self._reader is None:
            self._reader = self._build(self._stack.enter_context(self.checkpoints.saver()))
        return self._reader

    def close(self) -> None:
        self._stack.close()

    @contextmanager
    def working(self, claim_id: str) -> Iterator[bool]:
        """True for the one caller in this process that gets to work the claim; False while another is at it."""
        with self._lock:
            free = claim_id not in self._working
            self._working.add(claim_id)
        try:
            yield free
        finally:
            if free:
                with self._lock:
                    self._working.discard(claim_id)


@contextmanager
def open_env(settings: Settings, model=None, model_id: str | None = None) -> Iterator[AgentEnv]:
    """Everything the agent needs in a process: its connections, its checkpoints and its graph."""
    if model is None:
        if settings.agent_provider == "scripted":
            from agent.scripted import ScriptedChatModel

            model, model_id = ScriptedChatModel(), "scripted"
        else:
            from agent.models import chat_model

            model, model_id = chat_model(settings), settings.agent_model
    engine = make_engine(settings, max_connections=RECORD_CONNECTIONS)
    warm_up(engine, RECORD_CONNECTIONS)
    warm_up_tracing()
    try:
        with make_checkpoints(settings) as checkpoints:
            env = AgentEnv(engine, Reference(settings.data_dir), settings, checkpoints, model, model_id)
            try:
                yield env
            finally:
                env.close()
    finally:
        engine.dispose()


def _config(env: AgentEnv, claim_id: str, thread_id: str, run_id: int | None) -> dict:
    return {
        "configurable": {"thread_id": thread_id, "run_id": run_id,
                         "deadline": time.monotonic() + env.settings.agent_claim_timeout_s},
        "recursion_limit": 3 * env.settings.agent_max_model_calls + 10,  # model, tools, tools_done per call
        **trace_config(claim_id, thread_id, env.model_id),
    }


def invoke(env: AgentEnv, claim_id: str, thread_id: str, graph_input, run_id: int | None) -> dict:
    """One graph run, timed: it ends at the claim's end or at a pause, so a wait for a sign-off is never counted."""
    started_at, start = datetime.now(timezone.utc), env.clock()
    try:
        # In a context of its own: Langfuse takes the session from a trace's root, so a claim worked inside another
        # traced call would lose its work from the claim's session.
        config = _config(env, claim_id, thread_id, run_id)
        with env.claim_graph() as graph:
            return contextvars.Context().run(graph.invoke, graph_input, config, durability="sync")
    finally:
        record_work_time(env.engine, claim_id, run_id, started_at, env.clock() - start)
        flush_traces()


def _report(env: AgentEnv, claim_id: str) -> str:
    """The one line the queue agent gets, from the records."""
    claim = get_claim(env.engine, claim_id)
    decision = claim["decision"]
    if decision["decision"] != "approve":
        return f"{claim_id}: {decision['decision']}"
    payment = get_payment(env.engine, claim_id)
    state = "paid" if payment is not None and payment.status == "paid" else "held for sign-off"
    return f"{claim_id}: approve ${decision['payout']:,.2f}, {state}"


def _fail(env: AgentEnv, claim_id: str, thread_id: str, run_id: int | None, attempt: int, max_attempts: int, exc: Exception) -> str:
    """An error with no decision yet fails the attempt: the next one starts afresh, on a new thread."""
    message = f"{type(exc).__name__}: {exc}"
    status = "waiting" if attempt < max_attempts else "needs_handler"
    with env.engine.begin() as conn:
        conn.execute(update(claims).where(claims.c.claim_id == claim_id).values(
            status=status, attempts=attempt, last_error=message, thread_id=None,
        ))
    then = "It goes back in the queue." if status == "waiting" else "It is left for a handler."
    add_trace_event(env.engine, claim_id, "error", {"message": f"Attempt {attempt} of {max_attempts} failed ({message}). {then}"})
    if run_id is not None:
        todo_for_claim(env.engine, run_id, claim_id, "todo" if status == "waiting" else "left_for_handler", message)
    if status == "waiting":
        return f"{claim_id}: attempt {attempt} of {max_attempts} failed ({message}). It is back in the queue: try it again after the others."
    return f"{claim_id}: attempt {attempt} of {max_attempts} failed ({message}). Left for a handler."


def _drive(env: AgentEnv, claim_id: str, thread_id: str, run_id: int | None, attempt: int, max_attempts: int, graph_input) -> str:
    """Run the graph until it ends or pauses, and settle what that means for the claim."""
    try:
        result = invoke(env, claim_id, thread_id, graph_input, run_id)
        if result.get("__interrupt__") and not needs_sign_off(env.engine, env.ref, claim_id):
            invoke(env, claim_id, thread_id, None, run_id)  # signed off while the graph was pausing
    except Exception as exc:  # any model, tool or database failure; a kill or Ctrl-C is not one of these
        if get_claim(env.engine, claim_id)["decision"] is None:
            return _fail(env, claim_id, thread_id, run_id, attempt, max_attempts, exc)
        # The decision stands, so this must not start again: the next run carries on from the failed step.
        message = f"{type(exc).__name__}: {exc}"
        add_trace_event(env.engine, claim_id, "error", {"message": f"{message}. The decision stands; the next run carries on from here."})
        if run_id is not None:
            todo_for_claim(env.engine, run_id, claim_id, "in_progress", message)
        return f"{claim_id}: an error after the decision ({message}). The next run carries it on; do not try it again."
    line = _report(env, claim_id)
    if run_id is not None:
        todo_for_claim(env.engine, run_id, claim_id, "done", line.partition(": ")[2])
    return line


def work_claim(env: AgentEnv, claim_id: str, run_id: int | None = None, max_attempts: int = 2) -> str:
    """Make one attempt at one waiting claim and report the outcome in one line. A claim that is not waiting
    is never touched. Each attempt has its own thread, so a retry shares nothing with the attempt before it."""
    with env.engine.begin() as conn:
        row = conn.execute(select(claims.c.status, claims.c.attempts).where(claims.c.claim_id == claim_id)).mappings().first()
        if row is None:
            return f"{claim_id}: not worked, there is no such claim."
        attempt = row["attempts"] + 1  # claims.attempts counts the failed ones
        thread_id = f"{claim_id}-{attempt}"
        taken = conn.execute(
            update(claims).where(claims.c.claim_id == claim_id, claims.c.status == "waiting")
            .values(status="in_progress", run_id=run_id, thread_id=thread_id)
        )
    if taken.rowcount == 0:
        return f"{claim_id}: not worked, the claim is {row['status'].replace('_', ' ')}."
    with env.working(claim_id) as free:
        if not free:
            return f"{claim_id}: not worked, it is already being worked."
        add_trace_event(env.engine, claim_id, "status", {"status": "in_progress", "model": env.model_id, "attempt": attempt})
        if run_id is not None:
            todo_for_claim(env.engine, run_id, claim_id, "in_progress")
        return _drive(env, claim_id, thread_id, run_id, attempt, max_attempts, {"claim_id": claim_id})


def carry_on(env: AgentEnv, claim_id: str, run_id: int | None = None) -> str:
    """Pick a claim up from where it got to, on the same thread and with the same attempts. For a claim stopped
    partway, or signed off. Anything else is left alone. Its cost and time go with run_id: none after a sign-off."""
    with env.engine.connect() as conn:
        claim = conn.execute(
            select(claims.c.status, claims.c.thread_id, claims.c.attempts, claims.c.run_id, payments.c.status.label("payment"))
            .select_from(claims.outerjoin(payments, claims.c.claim_id == payments.c.claim_id))
            .where(claims.c.claim_id == claim_id)
        ).mappings().one()
    thread_id = claim["thread_id"]
    # a signed-off payment counts whatever the claim says: a stop can come between a sign-off's two statements
    resumable = claim["status"] == "in_progress" or (claim["status"] == "awaiting_approval" and claim["payment"] == "signed_off")
    if not resumable or thread_id is None:
        return f"{claim_id}: not worked, the claim is {claim['status'].replace('_', ' ')}."
    with env.working(claim_id) as free:
        if not free:
            return f"{claim_id}: not worked, it is already being worked."
        if claim["payment"] == "signed_off":
            graph_input = None  # straight on: the paused step runs again and finds the sign-off
        else:
            with env.claim_graph() as graph:
                state = graph.get_state({"configurable": {"thread_id": thread_id}})
            if not state.values:  # stopped before the first step was saved
                graph_input = {"claim_id": claim_id}
            elif state.next:
                graph_input = None
            else:
                return _report(env, claim_id)  # it had finished; only the last report was lost
            add_trace_event(env.engine, claim_id, "status", {"status": "in_progress", "note": "carried on"})
        attempt = claim["attempts"] + 1
        return _drive(env, claim_id, thread_id, run_id, attempt, env.settings.agent_max_attempts, graph_input)


def stopped_claims(engine: Engine) -> list[str]:
    """Claims left in progress with a thread: stopped partway, or signed off and not yet paid."""
    with engine.connect() as conn:
        return list(conn.execute(
            select(claims.c.claim_id).where(claims.c.status == "in_progress", claims.c.thread_id.is_not(None))
            .order_by(claims.c.claim_id)
        ).scalars())


def signed_off_claims(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        return list(conn.execute(
            select(payments.c.claim_id)
            .select_from(payments.join(claims, payments.c.claim_id == claims.c.claim_id))
            .where(payments.c.status == "signed_off", claims.c.status.in_(("in_progress", "awaiting_approval")))
            .order_by(payments.c.claim_id)
        ).scalars())
