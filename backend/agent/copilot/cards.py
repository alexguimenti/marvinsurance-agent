"""What the sidebar shows for each tool the Copilot calls: a card with an icon for its kind, the tool's name and its main input, and
its state. `carded` is the hook ToolNode runs around every tool call: it tells the app a step has begun, runs the tool, and tells the
app how it ended (done, refused, or failed), with the context layer's file and owner when the answer has them."""
import json
from typing import Any, Callable

from langchain_core.messages import ToolMessage
from langgraph.types import Command

from agent.copilot import app_api
from agent.operations import CONTEXT_TOOLS

REFUSED = "Refused: "  # what a tool's answer starts with when the app or the context layer said no
ACTIONS = frozenset({"start_dispatch", "start_evaluation", "send_back", "sign_off"})  # they change something: their cards stand out
KIND = {
    "list_claims": "claims", "get_claim": "claims", "get_claim_steps": "claims", "list_held_payouts": "claims", "what_needs_me": "claims",
    "get_policy": "customers", "find_customers": "customers", "get_customer": "customers",
    "get_latest_dispatch": "work", "list_evaluations": "work", "get_evaluation": "work", "get_settings": "work", "wait_for_run": "work",
    "check_success_criteria": "work",
    "search_memory": "memory",
    **{name: "context" for name in CONTEXT_TOOLS},
    **{name: "action" for name in ACTIONS},
}
OUTPUT_LIMIT = 2000


def kind_of(tool: str) -> str:
    return KIND.get(tool, "claims")


def main_input(tool: str, args: dict) -> str:
    """The one input a card shows beside the tool's name: `get_claim CLM-1012` shows `CLM-1012`."""
    if tool == "start_dispatch":
        return f"{args['workers']} agents" if args.get("workers") else "default agents"
    if tool == "start_evaluation":
        return f"{args.get('eval_set', '')}{' (tiny)' if args.get('tiny') else ''}".strip()
    if tool == "wait_for_run":
        return f"{args.get('kind', '')} {args.get('run_id', '')}".strip()
    for value in args.values():
        if value not in (None, ""):
            return str(value)
    return ""


def trim(value: Any, limit: int = OUTPUT_LIMIT) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + f"... ({len(text) - limit} more characters)"


def _content_of(result: ToolMessage | Command) -> tuple[str, str | None]:
    """The text a tool answered with, and its message's status."""
    if isinstance(result, Command):  # a tool that answers with an update (the sign-off's pause)
        messages = result.update.get("messages", []) if isinstance(result.update, dict) else []
        message = next((m for m in messages if isinstance(m, ToolMessage)), None)
        return (message.content if message else ""), None
    content = result.content if isinstance(result.content, str) else json.dumps(result.content, default=str)
    return content, getattr(result, "status", None)


def _sources_of(content: str) -> list | None:
    try:
        found = json.loads(content).get("sources")
    except (ValueError, AttributeError):
        return None
    return found if isinstance(found, list) and found else None


def carded(request, execute: Callable) -> ToolMessage | Command:
    """Record the step, run the tool, record how it ended. A tool that raises fails its card and then the turn."""
    asker = request.runtime.context
    call = request.tool_call
    name, args = call["name"], call.get("args") or {}
    step_id = app_api.record_step(asker.token, asker.turn_id, kind_of(name), name, main_input(name, args), args)
    try:
        result = execute(request)
    except BaseException as exc:
        app_api.finish_step(asker.token, step_id, "failed", None, None, trim(f"{type(exc).__name__}: {exc}", 500))
        raise
    content, status = _content_of(result)
    if status == "error":
        app_api.finish_step(asker.token, step_id, "failed", None, None, trim(content, 500))
    elif content.startswith(REFUSED):
        app_api.finish_step(asker.token, step_id, "refused", None, None, trim(content.removeprefix(REFUSED), 500))
    else:
        app_api.finish_step(asker.token, step_id, "done", trim(content), _sources_of(content) if kind_of(name) == "context" else None)
    return result
