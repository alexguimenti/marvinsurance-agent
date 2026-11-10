"""What the agents' steps look like on the record: trace events, usage and checks on a rationale."""
import re
from typing import Callable

from langchain_core.messages import AIMessage, ToolMessage

Emit = Callable[[str, dict], None]


def rationale_warnings(rationale: str) -> list[str]:
    warnings = []
    sentences = [s for s in re.split(r"[.!?]+(?:\s+|$)", rationale.strip()) if s]
    if not 2 <= len(sentences) <= 3:
        warnings.append(f"rationale has {len(sentences)} sentences, expected 2-3")
    if "section" not in rationale.lower():
        warnings.append("rationale does not name a wording section")
    return warnings


def call_usage(message: AIMessage) -> dict:
    """One reply's tokens, and its cost as OpenRouter reports it in usage.cost."""
    meta = message.usage_metadata or {}
    return {
        "input_tokens": meta.get("input_tokens", 0),
        "output_tokens": meta.get("output_tokens", 0),
        "reasoning_tokens": (meta.get("output_token_details") or {}).get("reasoning", 0),
        "cost_usd": ((message.response_metadata or {}).get("token_usage") or {}).get("cost"),
    }


class Usage:
    """Running total of what a claim cost: tokens, and dollars when the provider reports them."""

    def __init__(self, total: dict | None = None):
        total = total or {}
        self.calls = total.get("calls", 0)
        self.input_tokens = total.get("input_tokens", 0)
        self.output_tokens = total.get("output_tokens", 0)
        self.reasoning_tokens = total.get("reasoning_tokens", 0)
        self.cost_usd: float | None = total.get("cost_usd")

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


GATEWAY_REFUSAL = "Error: refused by the Gateway: "


def emit_message(message, on_event: Emit, calls: dict[str, dict], shown: Callable[[str], str] = lambda name: name) -> None:
    """Trace one message of an agent: tool calls, tool results, refusals and plain text. `shown` gives a tool the name
    a handler sees for it: the Gateway's on the Gateway route."""
    if isinstance(message, AIMessage):
        for call in message.tool_calls:
            calls[call["id"]] = call["args"]
            if call["name"] != "record_decision":  # the decision has an event of its own
                on_event("tool_call", {"name": shown(call["name"]), "args": call["args"]})
        if message.content and not message.tool_calls:
            on_event("message", {"text": str(message.content)})
    elif isinstance(message, ToolMessage):
        content = str(message.content)
        if getattr(message, "status", "success") == "error":  # the app, the context layer or the Gateway said no
            from agent.operations import CONTEXT_TOOLS

            by = "gateway" if content.startswith(GATEWAY_REFUSAL) else "context" if message.name in CONTEXT_TOOLS else "app"
            reason = content.removeprefix(GATEWAY_REFUSAL).removeprefix("Error: ")
            on_event("refusal", {"by": by, "tool": shown(message.name), "reason": reason})
            return
        if message.name == "record_decision" and not content.startswith("Error:"):
            return
        artifact = getattr(message, "artifact", None)
        if isinstance(artifact, dict) and "sources" in artifact:  # a lookup in the context layer: what and where from
            on_event("lookup", {"tool": shown(message.name), "args": calls.get(message.tool_call_id, {}),
                                "sources": artifact["sources"], "found": artifact.get("found", "")})
            return
        on_event("tool_result", {"name": shown(message.name), "content": content})
