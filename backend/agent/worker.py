"""Working one claim: for the first time, carrying on after a stop or a sign-off, or starting afresh after an error."""
import contextvars
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterator

import httpx
from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError
from langgraph.types import Command

from agent import api
from agent.checkpoints import Checkpoints, make_checkpoints
from agent.heartbeat import heartbeat
from agent.graph import build_claim_graph
from agent.memory import open_store
from agent.route import make_route
from agent.tracing import flush_traces, trace_config, warm_up_tracing
from app.config import Settings
from app.reference import Reference

# Worth waiting out: the caller retries, and the claim carries on from its last saved step as the same attempt.
# A transport error is the backend restarting or out of reach: no attempt is used up.
RETRYABLE = (ModelRateLimitError, ModelTimeoutError, httpx.TransportError)
# What a paused graph is resumed with. The node reads the handler's answer from the app's record; the value only has to be one
# LangGraph takes as an answer, and an empty one it does not.
RESUME = {"resumed": True}
CREDIT_OUT = "OpenRouter's credit limit for the key was reached. Raise the key's limit, then start again: nothing is lost."


def out_of_credit(exc: BaseException) -> bool:
    """OpenRouter answers 402 when the key's credit or daily limit cannot pay for the call. It is nobody's fault but
    the key's: not a claim's failure, and every other claim would meet the same answer."""
    return getattr(exc, "status_code", None) == 402


def _kind_of(run_id: int) -> str | None:
    """dispatch or evaluation; None for a run the backend does not know."""
    try:
        return api.get_run(run_id)["kind"]
    except LookupError:
        return None


@dataclass
class AgentEnv:
    ref: Reference
    settings: Settings
    checkpoints: Checkpoints
    model: object | None  # a fixed model (scripted, or a test's stub); None builds each version's own
    model_id: str | None
    improver_model: object | None = None  # a test's own, for the improver
    screen_model: object | None = None  # the security check's model: a test's stub, or the scripted one; None builds the real one
    memory_store: object | None = None  # a test's memory; None reads UNDERCOVER_MEMORY_ID each time a graph is built
    route: object | None = None  # a test's route to the tools; None reads the environment each time a graph is built
    clock: Callable[[], float] = time.monotonic  # a test can move it on without sleeping
    out_of_credit: threading.Event = field(default_factory=threading.Event)  # set by the first 402; a run stops starting claims
    _working: set = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _reader: object = None
    _stack: ExitStack = field(default_factory=ExitStack)
    _models: dict = field(default_factory=dict)

    def config_for(self, run_id: int | None) -> dict:
        """The configuration version a run uses: its instructions, model and reasoning effort, and nothing else."""
        return api.configuration(run_id)

    def effective(self, config: dict) -> tuple[str, str]:
        """The model and reasoning effort a version runs with. A fixed model (scripted, or a test's) has no effort."""
        if self.model is not None:
            return self.model_id or "fixed", "default"
        return config["model"], config["reasoning_effort"]

    def model_for(self, config: dict):
        if self.model is not None:
            return self.model
        key = (config["model"], config["reasoning_effort"])
        if key not in self._models:
            from agent.models import chat_model

            self._models[key] = chat_model(self.settings, *key)
        return self._models[key]

    def screen(self):
        """The security check's model and its id. It is outside the versioned configuration: no version can change it."""
        if self.screen_model is not None:
            return self.screen_model, "screen/stub"
        if "screen" not in self._models:
            from agent.models import chat_model

            self._models["screen"] = chat_model(self.settings, *self.settings.screening_model, max_retries=0)
        return self._models["screen"], self.settings.screening_model[0]

    def _build(self, saver, config: dict, evaluation: bool = False):
        screen_model, screen_model_id = self.screen()
        return build_claim_graph(self.ref, self.model_for(config), self.effective(config)[0],
                                 self.settings.agent_max_model_calls, saver, config["instructions"],
                                 screen_model, screen_model_id, self.route or make_route(),
                                 f"UnderCover Agent, configuration version {config['version']}",
                                 None if evaluation else (self.memory_store or open_store()))

    @contextmanager
    def claim_graph(self, run_id: int | None = None, config: dict | None = None) -> Iterator:
        """The claim graph for a run's version, on a saver of its own for one claim run: a saver takes turns between
        its callers."""
        config = config or self.config_for(run_id)
        # an evaluation's scores must not depend on memory: the kind of run decides it when its graphs are built
        evaluation = run_id is not None and _kind_of(run_id) == "evaluation"
        with self.checkpoints.saver() as saver:
            yield self._build(saver, config, evaluation)

    @property
    def graph(self):
        """A graph on a saver of its own for looking at threads from outside a claim run, as tests do."""
        if self._reader is None:
            self._reader = self._build(self._stack.enter_context(self.checkpoints.saver()), self.config_for(None))
        return self._reader

    def close(self) -> None:
        self._stack.close()

    def is_working(self, claim_id: str) -> bool:
        with self._lock:
            return claim_id in self._working

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
def open_env(settings: Settings, model=None, model_id: str | None = None, improver_model=None, screen_model=None,
             route=None, memory_store=None) -> Iterator[AgentEnv]:
    """Everything the agent needs in a process: its connections, its checkpoints and its graph."""
    if model is None:
        if settings.agent_provider == "scripted":
            from agent.scripted import ScriptedChatModel, ScriptedScreenModel

            model, model_id = ScriptedChatModel(), "scripted"
            screen_model = screen_model or ScriptedScreenModel()
        else:
            model, model_id = None, None  # each configuration version builds its own model
    warm_up_tracing()
    with make_checkpoints(settings) as checkpoints:  # the one connection the agent keeps to the database
        env = AgentEnv(Reference(settings.data_dir), settings, checkpoints, model, model_id, improver_model, screen_model,
                       memory_store=memory_store, route=route)
        try:
            yield env
        finally:
            env.close()


def _config(env: AgentEnv, claim_id: str, thread_id: str, run_id: int | None, model_id: str, round_id: int | None = None) -> dict:
    return {
        "configurable": {"thread_id": thread_id, "run_id": run_id,
                         "deadline": time.monotonic() + env.settings.agent_claim_timeout_s},
        "recursion_limit": 3 * env.settings.agent_max_model_calls + 10,  # model, tools, tools_done per call
        **trace_config(claim_id, thread_id, model_id, run_id, round_id),
    }


def invoke(env: AgentEnv, claim_id: str, thread_id: str, graph_input, run_id: int | None) -> dict:
    """One graph run, timed: it ends at the claim's end or at a pause, so a wait for a sign-off is never counted."""
    started_at, start = datetime.now(timezone.utc), env.clock()
    try:
        # In a context of its own: Langfuse takes the session from a trace's root, so a claim worked inside another
        # traced call would lose its work from the claim's session.
        version = env.config_for(run_id)
        config = _config(env, claim_id, thread_id, run_id, env.effective(version)[0], version.get("round_id"))
        with env.claim_graph(run_id, config=version) as graph:
            return contextvars.Context().run(graph.invoke, graph_input, config, durability="sync")
    finally:
        api.record_work_time(claim_id, run_id, started_at, env.clock() - start)
        flush_traces()


def claim_paused(env: AgentEnv, claim_id: str) -> bool:
    """A claim is paused when its thread has an interrupt waiting for an answer."""
    thread_id = api.claim_state(claim_id)["thread_id"]
    if thread_id is None:
        return False
    with env.claim_graph() as graph:
        return bool(graph.get_state({"configurable": {"thread_id": thread_id}}).interrupts)


def _report(env: AgentEnv, claim_id: str) -> str:
    """The claim's outcome in one line, from the records."""
    state = api.claim_state(claim_id)
    decision, payment = state["decision"], state["payment"]
    if decision["decision"] != "approve":
        return f"{claim_id}: {decision['decision']}"
    paid = "paid" if payment is not None and payment["status"] == "paid" else "held for sign-off"
    return f"{claim_id}: approve ${decision['payout']:,.2f}, {paid}"


def _fail(env: AgentEnv, claim_id: str, thread_id: str, run_id: int | None, attempt: int, max_attempts: int, exc: Exception) -> str:
    """An error with no decision yet fails the attempt: the next one starts afresh, on a new thread."""
    message = f"{type(exc).__name__}: {exc}"
    status = api.fail(claim_id, attempt, max_attempts, message)
    then = "It goes back in the queue." if status == "waiting" else "It is left for a handler."
    api.add_step(claim_id, "error", {"message": f"Attempt {attempt} of {max_attempts} failed ({message}). {then}"})
    if status == "waiting":
        return f"{claim_id}: attempt {attempt} of {max_attempts} failed ({message}). It is back in the queue."
    return f"{claim_id}: attempt {attempt} of {max_attempts} failed ({message}). Left for a handler."


def _settle(env: AgentEnv, claim_id: str, thread_id: str, run_id: int | None, attempt: int, max_attempts: int) -> str:
    """A run of the graph that ended: the claim's outcome. Ending with no decision on record is a failed attempt, never a
    claim to report: a thread cannot be carried on from an end, so the next attempt starts afresh."""
    if api.claim_state(claim_id)["decision"] is None:
        return _fail(env, claim_id, thread_id, run_id, attempt, max_attempts, ValueError("the saved progress ended with no decision"))
    return _report(env, claim_id)


def _drive(env: AgentEnv, claim_id: str, thread_id: str, run_id: int | None, attempt: int, max_attempts: int, graph_input) -> str:
    """Run the graph until it ends or pauses, and settle what that means for the claim."""
    try:
        result = invoke(env, claim_id, thread_id, graph_input, run_id)
        if result.get("__interrupt__") and (api.claim_state(claim_id)["payment"] or {}).get("status") == "paid":
            invoke(env, claim_id, thread_id, Command(resume=RESUME), run_id)  # signed off while the graph was pausing
    except RETRYABLE as exc:  # not settled here: the claim stays in progress on its thread, for the retry
        api.add_step(claim_id, "error", {"message": f"{type(exc).__name__}: {exc}. The agent waits and will carry on from here."})
        raise
    except Exception as exc:  # any model, tool or database failure; a kill or Ctrl-C is not one of these
        if out_of_credit(exc):  # the claim stays in progress, with no failed attempt, for the next run
            env.out_of_credit.set()
            api.add_step(claim_id, "error", {"message": f"{CREDIT_OUT} This claim stays in progress."})
            return f"{claim_id}: not decided, {CREDIT_OUT}"
        if api.claim_state(claim_id)["decision"] is None:
            return _fail(env, claim_id, thread_id, run_id, attempt, max_attempts, exc)
        # The decision stands, so this must not start again: the next run carries on from the failed step.
        message = f"{type(exc).__name__}: {exc}"
        api.add_step(claim_id, "error", {"message": f"{message}. The decision stands; the next run carries on from here."})
        return f"{claim_id}: an error after the decision ({message}). The next run carries it on."
    return _settle(env, claim_id, thread_id, run_id, attempt, max_attempts)


def work_claim(env: AgentEnv, claim_id: str, run_id: int | None = None, max_attempts: int = 2) -> str:
    """Make one attempt at one waiting claim and report the outcome in one line. A claim that is not waiting
    is never touched. Each attempt has its own thread, so a retry shares nothing with the attempt before it."""
    try:
        taken = api.take(claim_id, run_id)
    except LookupError:
        return f"{claim_id}: not worked, there is no such claim."
    attempt, thread_id = taken["attempt"], taken["thread_id"]
    if not taken["taken"]:
        return f"{claim_id}: not worked, the claim is {taken['status'].replace('_', ' ')}."
    with env.working(claim_id) as free:
        if not free:
            return f"{claim_id}: not worked, it is already being worked."
        api.add_step(claim_id, "status", {"status": "in_progress", "model": env.effective(env.config_for(run_id))[0], "attempt": attempt})
        return _drive(env, claim_id, thread_id, run_id, attempt, max_attempts, {"claim_id": claim_id})


def carry_on(env: AgentEnv, claim_id: str, run_id: int | None = None) -> str:
    """Pick a claim up from where it got to, on the same thread and with the same attempts. For a claim stopped
    partway. Anything else is left alone. Its cost and time go with run_id."""
    claim = api.claim_state(claim_id)
    thread_id = claim["thread_id"]
    if claim["status"] != "in_progress" or thread_id is None:
        return f"{claim_id}: not worked, the claim is {claim['status'].replace('_', ' ')}."
    with env.working(claim_id) as free:
        if not free:
            return f"{claim_id}: not worked, it is already being worked."
        with env.claim_graph(run_id) as graph:
            state = graph.get_state({"configurable": {"thread_id": thread_id}})
        if not state.values:  # stopped before the first step was saved
            graph_input = {"claim_id": claim_id}
        elif state.next:
            graph_input = None
        else:  # the saved progress ended: if there is a decision only the last report was lost, and if not it is a failed attempt
            return _settle(env, claim_id, thread_id, run_id, claim["attempts"] + 1, env.settings.agent_max_attempts)
        if run_id is not None:  # its steps are traced under the run carrying it on
            api.carry_on(claim_id, run_id)
        api.add_step(claim_id, "status", {"status": "in_progress", "note": "carried on"})
        attempt = claim["attempts"] + 1
        return _drive(env, claim_id, thread_id, run_id, attempt, env.settings.agent_max_attempts, graph_input)


def resume_refusal(env: AgentEnv, claim_id: str) -> str | None:
    """Why a claim cannot be resumed, or None when it can: only a claim whose thread is paused, waiting for an answer. A
    sent-back claim is taken for work here, once: a second caller is refused."""
    try:
        paused = claim_paused(env, claim_id)
        status = api.claim_state(claim_id)["status"]
    except LookupError:
        return f"there is no claim {claim_id}"
    if not paused:
        return f"{claim_id} is not waiting for a handler's answer"
    if status == "sent_back" and not api.take_sent_back(claim_id):
        return f"{claim_id} is already being worked"
    return None


def resume(env: AgentEnv, claim_id: str, run_id: int | None = None) -> str:
    """Resume a paused claim with the handler's answer, read from the app's record. The node that paused finds the claim
    paid and the work ends, or sent back, and the agent decides again; a claim still waiting pauses again. While it works it
    tells the app every minute, so a dispatch leaves the claim alone."""
    claim = api.claim_state(claim_id)
    with env.working(claim_id) as free:
        if not free:
            return f"{claim_id}: not worked, it is already being worked."
        with heartbeat(lambda: api.heard(claim_id)):
            return _drive(env, claim_id, claim["thread_id"], run_id, claim["attempts"] + 1, env.settings.agent_max_attempts,
                          Command(resume=RESUME))


def work(env: AgentEnv, claim_id: str, run_id: int | None) -> str:
    """The one way in for the dispatcher: carry on a claim in progress, or start a waiting one, and report it in one
    line. A rate limit or a timeout is let through unsettled, so that a retry carries the claim on."""
    try:
        status = api.claim_state(claim_id)["status"]
    except LookupError:
        return f"{claim_id}: not worked, there is no such claim."
    if status == "waiting":
        line = work_claim(env, claim_id, run_id, env.settings.agent_max_attempts)
    elif status == "sent_back":  # a send back the agent never received: this dispatch takes it, with the note
        reason = resume_refusal(env, claim_id)
        if reason is None:
            line = resume(env, claim_id, run_id)
        elif api.claim_state(claim_id)["status"] == "sent_back" or "already being worked" in reason:
            line = f"{claim_id}: not worked, {reason}."
        else:  # taken, but its thread is not paused any more: start it again as a new attempt
            claim = api.claim_state(claim_id)
            line = _fail(env, claim_id, claim["thread_id"], run_id, claim["attempts"] + 1, env.settings.agent_max_attempts,
                         ValueError("the saved progress was not waiting for the handler's note"))
    else:
        line = carry_on(env, claim_id, run_id)  # it leaves alone anything it cannot carry on, and says so
    _clear_finished_copy(env, claim_id)
    return line


def _clear_finished_copy(env: AgentEnv, claim_id: str) -> None:
    """An evaluation's copy is never carried on or signed off, so once it is decided, held or left for a handler,
    its saved progress goes. A held approval counts as finished."""
    row = api.claim_state(claim_id)
    if row["copy_of"] is None or row["status"] not in ("decided", "awaiting_approval", "needs_handler"):
        return
    with env.claim_graph() as graph:
        for attempt in range(1, row["attempts"] + 2):  # every thread the copy had, one per attempt
            graph.checkpointer.delete_thread(f"{claim_id}-{attempt}")
