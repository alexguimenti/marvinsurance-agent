"""A turn of a conversation, worked on a thread of the Copilot's app, and the handler's answer to a confirmation.

Both start by asking the app who is signed in. If the app does not accept the sign-in, the work ends there, before any call to the
model. Then the graph runs on the conversation's saved state (its thread is the conversation's id), and the work ends in one of three
ways, each reported to the app with the handler's sign-in: the graph paused for a confirmation (the app builds the card from its
records), it finished with an answer, or it failed.

A `CopilotEnv` is what the Copilot needs in a process: its settings, its checkpoints in Supabase (or a SQLite file on a laptop), its
model, its memory and the context layer's tools. The model, the memory and the tools are made when first needed, never at import."""
import logging
import threading
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import Iterator

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from agent.checkpoints import Checkpoints, make_checkpoints
from agent.connection import Refused
from agent.copilot import app_api, memory
from agent.copilot.asker import Asker
from agent.copilot.graph import MAX_MODEL_CALLS, answer_of, build_copilot_graph, load_instructions
from agent.copilot.payloads import Answer, Turn
from agent.settings import AgentSettings

log = logging.getLogger(__name__)
REASON_LIMIT = 300
RECURSION_LIMIT = 100  # LangGraph steps: far more than 25 model calls need


@dataclass
class CopilotEnv:
    settings: AgentSettings
    checkpoints: Checkpoints
    model: object | None = None  # a test's; None is the scripted model or the real one, by the provider
    store: object | None = None  # a test's memory; None reads UNDERCOVER_COPILOT_MEMORY_ID each time a graph is built
    context_tools: list | None = None  # a test's; None loads them on the first turn
    _made: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _model(self):
        if self.model is not None:
            return self.model
        with self._lock:
            if "model" not in self._made:
                if self.settings.agent_provider == "scripted":
                    from agent.copilot.scripted import ScriptedCopilotModel

                    self._made["model"] = ScriptedCopilotModel()
                else:
                    from agent.models import chat_model

                    self._made["model"] = chat_model(self.settings, *self.settings.copilot_model)
            return self._made["model"]

    def _tools(self) -> list:
        with self._lock:
            if "tools" not in self._made:
                from agent.copilot.context_tools import load_context_tools
                from agent.copilot.tools import build_tools

                found = self.context_tools if self.context_tools is not None else load_context_tools()
                self._made["tools"] = build_tools(found)
            return self._made["tools"]

    @contextmanager
    def graph(self) -> Iterator:
        """The Copilot's graph on a saver of its own for one piece of work: a saver takes turns between its callers."""
        with self.checkpoints.saver() as saver:
            yield build_copilot_graph(self._model(), saver, self._tools(), self.store or memory.open_copilot_store(),
                                      load_instructions(), MAX_MODEL_CALLS)


@contextmanager
def open_env(settings: AgentSettings, model=None, store=None, context_tools=None) -> Iterator[CopilotEnv]:
    """Everything the Copilot needs in a process: its connection to the checkpoints, and what a test supplies."""
    with ExitStack() as stack:
        checkpoints = stack.enter_context(make_checkpoints(settings))
        yield CopilotEnv(settings, checkpoints, model, store, context_tools)


def _reason(exc: BaseException, token: str) -> str:
    """Why a turn failed, short, and never with the sign-in in it."""
    return f"{type(exc).__name__}: {exc}".replace(token, "[sign-in]")[:REASON_LIMIT]


def _asker(body: Turn | Answer) -> Asker | None:
    """Ask the app who is signed in. None when it does not accept the sign-in: nothing more is done."""
    try:
        handler = app_api.who(body.token)
    except Exception as exc:
        if isinstance(exc, Refused) and exc.status_code in (401, 403):  # the sign-in was not accepted: nothing can be reported with it either
            log.warning("turn %s: the app did not accept the sign-in (%s)", body.turn_id, exc.status_code)
            return None
        # the app did not answer, or answered with an error (a restart, a dropped connection): say so, so the turn does not wait to be stopped
        log.warning("turn %s: the sign-in could not be checked (%s)", body.turn_id, type(exc).__name__)
        try:
            app_api.fail(body.token, body.turn_id, f"The Copilot could not reach the app to check who is signed in ({type(exc).__name__}). Send the message again.")
        except Exception:
            log.warning("turn %s: the app could not be told that it failed", body.turn_id)
        return None
    return Asker(body.token, handler, body.conversation_id, body.turn_id)


def _drive(env: CopilotEnv, asker: Asker, graph_input) -> None:
    config = {"configurable": {"thread_id": asker.conversation_id}, "recursion_limit": RECURSION_LIMIT}
    try:
        with env.graph() as graph:
            out = graph.invoke(graph_input, config, context=asker)
            paused = graph.get_state(config).interrupts
        if paused:
            app_api.pause(asker.token, asker.turn_id, paused[0].value["claim_id"])
        else:
            app_api.end(asker.token, asker.turn_id, answer_of(out["messages"]), out.get("model_calls", 0), out.get("cost_usd"))
    except Exception as exc:  # the turn fails with why; the next message starts a new one
        log.error("turn %s failed: %s", asker.turn_id, _reason(exc, asker.token))  # the reason is scrubbed: a log never holds the sign-in
        try:
            app_api.fail(asker.token, asker.turn_id, _reason(exc, asker.token))
        except Exception:
            log.warning("turn %s: the app could not be told that it failed", asker.turn_id)


def run_turn(env: CopilotEnv, body: Turn) -> None:
    asker = _asker(body)
    if asker is not None:
        _drive(env, asker, {"messages": [HumanMessage(body.message)], "model_calls": 0, "cost_usd": 0.0, "sign_off": None})


def answer(env: CopilotEnv, body: Answer) -> None:
    asker = _asker(body)
    if asker is not None:
        _drive(env, asker, Command(resume=body.answer))
