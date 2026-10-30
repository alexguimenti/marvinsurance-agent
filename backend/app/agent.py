import re
import sqlite3
import time
from pathlib import Path
from typing import Callable, Literal, Protocol

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, model_validator

from app.config import Settings
from app.reference import Reference
from app.tools import build_tools

OPENROUTER_URL = "https://openrouter.ai/api/v1"
Emit = Callable[[str, dict], None]


class CalcStep(BaseModel):
    step: str
    amount: float


class Decision(BaseModel):
    decision: Literal["approve", "decline", "refer"]
    payout: float = 0.0
    calculation: list[CalcStep] = []
    rationale: str
    citations: list[str] = []

    @model_validator(mode="after")
    def _payout_only_on_approve(self):
        if self.decision != "approve" and self.payout != 0:
            raise ValueError("payout must be 0 unless the decision is approve")
        if self.payout < 0:
            raise ValueError("payout cannot be negative")
        return self


def rationale_warnings(rationale: str) -> list[str]:
    warnings = []
    sentences = [s for s in re.split(r"[.!?]+(?:\s+|$)", rationale.strip()) if s]
    if not 2 <= len(sentences) <= 3:
        warnings.append(f"rationale has {len(sentences)} sentences, expected 2-3")
    if "section" not in rationale.lower():
        warnings.append("rationale does not name a wording section")
    return warnings


def load_instructions(name: Literal["claim", "run"]) -> str:
    return (Path(__file__).parent / f"instructions_{name}.md").read_text(encoding="utf-8")


class Usage:
    """Running total of what a claim cost: tokens, and dollars when the provider reports them."""

    def __init__(self):
        self.calls = self.input_tokens = self.output_tokens = self.reasoning_tokens = 0
        self.cost_usd: float | None = None

    def add(self, message: AIMessage) -> None:
        self.calls += 1
        meta = message.usage_metadata or {}
        self.input_tokens += meta.get("input_tokens", 0)
        self.output_tokens += meta.get("output_tokens", 0)
        self.reasoning_tokens += (meta.get("output_token_details") or {}).get("reasoning", 0)
        cost = ((message.response_metadata or {}).get("token_usage") or {}).get("cost")
        if cost is not None:
            self.cost_usd = round((self.cost_usd or 0.0) + cost, 8)

    def as_dict(self) -> dict | None:
        if self.calls == 0:
            return None
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cost_usd": self.cost_usd,
        }


class Agent(Protocol):
    model_id: str
    usage: dict | None  # what the last decide() cost, or None when unknown

    def decide(self, claim_id: str, on_event: Emit) -> Decision: ...


class LangChainAgent:
    def __init__(self, model, conn: sqlite3.Connection, ref: Reference, model_id: str,
                 max_steps: int = 40, timeout_s: float = 300):
        self.model = model
        self.conn = conn
        self.ref = ref
        self.model_id = model_id
        self.max_steps = max_steps
        self.timeout_s = timeout_s
        self.usage: dict | None = None

    def decide(self, claim_id: str, on_event: Emit) -> Decision:
        agent = create_agent(
            self.model,
            build_tools(self.conn, self.ref, claim_id),
            system_prompt=load_instructions("claim"),
            response_format=ToolStrategy(Decision),
        )
        prompt = f"Decide claim {claim_id}. Start by reading it with get_claim."
        self.usage = None

        def keep_usage(usage: dict) -> None:
            self.usage = usage  # kept up to date, so a failed attempt still reports its cost

        decision = run_graph(agent, prompt, self.max_steps, on_event, keep_usage, self.timeout_s)
        if decision is None:
            raise ValueError("the agent finished without a decision")
        on_event("decision", decision.model_dump())
        return decision


def _emit_message(message, on_event: Emit, calls: dict[str, dict]) -> None:
    """Trace one message of a LangChain agent: tool calls, tool results and plain text."""
    if isinstance(message, AIMessage):
        for call in message.tool_calls:
            calls[call["id"]] = call["args"]
            if call["name"] != "Decision":
                on_event("tool_call", {"name": call["name"], "args": call["args"]})
        if message.content and not message.tool_calls:
            on_event("message", {"text": str(message.content)})
    elif isinstance(message, ToolMessage) and message.name != "Decision":
        content = str(message.content)
        if message.name == "read_section" and not content.startswith(("Error:", "You already read")):
            content = _section_summary(calls.get(message.tool_call_id, {}), content)  # the trace records what was read, not a copy of it
        on_event("tool_result", {"name": message.name, "content": content})


def run_graph(graph, message: str, max_steps: int, on_event: Emit,
              on_usage: Callable[[dict | None], None], timeout_s: float | None = None):
    """Stream a LangChain agent from one request, tracing every message and keeping its usage up to date.
    Returns its structured response, or None when it has none."""
    total = Usage()
    calls: dict[str, dict] = {}  # tool call id -> its arguments, to describe the result
    deadline = time.monotonic() + timeout_s if timeout_s else None
    result = None
    config = {"recursion_limit": max_steps}
    for chunk in graph.stream({"messages": [HumanMessage(message)]}, stream_mode="updates", config=config):
        if deadline and time.monotonic() > deadline:
            raise TimeoutError(f"the agent took longer than {timeout_s:g} seconds")
        for update in chunk.values():
            if not update:
                continue
            for msg in update.get("messages", []):
                if isinstance(msg, AIMessage):
                    total.add(msg)
                    on_usage(total.as_dict())
                _emit_message(msg, on_event, calls)
            if "structured_response" in update:
                result = update["structured_response"]
    return result


def _section_summary(args: dict, content: str) -> str:
    """'[claims_manual section 7: Interpretation notes, 3120 characters]' for a section the agent read."""
    heading = content.splitlines()[0].lstrip("# ").strip()
    title = heading.partition(". ")[2] or heading
    return f"[{args.get('document')} section {args.get('section')}: {title}, {len(content)} characters]"


def build_agent(settings: Settings, conn: sqlite3.Connection, ref: Reference) -> Agent:
    if settings.agent_provider == "scripted":
        from app.scripted import ScriptedAgent  # stand-in for tests and e2e runs

        return ScriptedAgent(conn)
    return LangChainAgent(chat_model(settings), conn, ref, settings.agent_model,
                          max_steps=settings.agent_max_steps, timeout_s=settings.agent_claim_timeout_s)


def chat_model(settings: Settings) -> ChatOpenAI:
    """The configured OpenRouter model, shared by the claim agent and the orchestrator."""
    return ChatOpenAI(
        model=settings.agent_model,
        api_key=settings.openrouter_api_key,
        base_url=OPENROUTER_URL,
        temperature=settings.agent_temperature,
        timeout=settings.agent_claim_timeout_s,
        max_retries=settings.agent_step_retries,
        extra_body={
            "usage": {"include": True},  # OpenRouter then reports the dollar cost of each call
            **({"reasoning": {"effort": settings.agent_reasoning_effort}} if settings.agent_reasoning_effort else {}),
        },
    )
