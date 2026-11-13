"""The Copilot's tools. Each one reads who is asking from the graph's context (`ToolRuntime`) and calls the app with that handler's
sign-in, so the Copilot sees and does exactly what the handler can on the dashboard, and the app still checks every action.

A refusal from the app (`Refused`, or an unknown record) comes back to the model as "Refused: <reason>", so it can tell the handler.
Anything else (the app not answering) is raised: the turn fails, and the sidebar says what went wrong."""
import functools
import json
from typing import Any, Callable, Literal

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command

from agent.connection import Refused
from agent.copilot import app_api, criteria, waiting
from agent.copilot.asker import Asker
from agent.copilot.cards import REFUSED

Runtime = ToolRuntime[Asker]


def as_text(value: Any) -> str:
    return json.dumps(value, default=str)


def refusal_safe(function: Callable[..., Any]) -> Callable[..., str]:
    """Run a tool; answer in text, with a refusal as "Refused: <reason>"."""

    @functools.wraps(function)  # keeps the signature: the tool's schema comes from it
    def run(*args: Any, **kwargs: Any) -> str:
        try:
            result = function(*args, **kwargs)
            return result if isinstance(result, (Command, str)) else as_text(result)  # a Command is the sign-off's pause, a str a refusal
        except (Refused, LookupError) as exc:
            return f"{REFUSED}{exc}"

    return run


def agents_at_once(token: str) -> int:
    """The number of claim agents in the settings, which a start uses when the handler does not say."""
    rows = {r["name"]: r["value"] for r in app_api.get(token, "/api/settings")["settings"]}
    return int(rows["agents_at_once"])


def build_tools(context_tools: list[BaseTool], extra: list[BaseTool] | None = None) -> list[BaseTool]:
    """The Copilot's tools: lookups in the app, the agent's work, decisions, and the context layer's tools."""

    @tool
    @refusal_safe
    def list_claims(runtime: Runtime) -> Any:
        """List every claim in the queue with its status, decision and payout."""
        return app_api.get(runtime.context.token, "/api/claims")

    @tool
    @refusal_safe
    def get_claim(claim_id: str, runtime: Runtime) -> Any:
        """Get one claim in full: the claim, its policy and customer, its decision, its payout, any decision sent back with its note, and what the agent looked up."""
        return app_api.get(runtime.context.token, f"/api/claims/{claim_id}")

    @tool
    @refusal_safe
    def get_claim_steps(claim_id: str, runtime: Runtime) -> Any:
        """List the agent's steps on a claim, in order."""
        return app_api.get(runtime.context.token, f"/api/claims/{claim_id}/trace")

    @tool
    @refusal_safe
    def list_held_payouts(runtime: Runtime) -> Any:
        """List the payouts held for a handler's sign-off, each with its claim, customer, amount and the agent's reason."""
        return app_api.get(runtime.context.token, "/api/approvals")

    @tool
    @refusal_safe
    def get_policy(policy_id: str, runtime: Runtime) -> Any:
        """Get a policy by its id: plan, add-ons, purchase date, trip dates and travelers."""
        return app_api.get(runtime.context.token, f"/api/policies/{policy_id}")

    @tool
    @refusal_safe
    def find_customers(name: str, runtime: Runtime) -> Any:
        """Find customers by part of their name, or by their exact customer id."""
        return app_api.get(runtime.context.token, "/api/customers", params={"name": name})

    @tool
    @refusal_safe
    def get_customer(customer_id: str, runtime: Runtime) -> Any:
        """Get a customer with their policies, their earlier claims and their claims in our queue."""
        return app_api.get(runtime.context.token, f"/api/customers/{customer_id}")

    @tool
    @refusal_safe
    def get_latest_dispatch(runtime: Runtime) -> Any:
        """Get the latest dispatch with its claims, its cost and time and, once it has ended, its summary."""
        return app_api.get(runtime.context.token, "/api/dispatches/latest")

    @tool
    @refusal_safe
    def list_evaluations(runtime: Runtime) -> Any:
        """List the evaluations, newest first, each with its measures."""
        return app_api.get(runtime.context.token, "/api/evaluations")

    @tool
    @refusal_safe
    def get_evaluation(run_id: int, runtime: Runtime) -> Any:
        """Get one evaluation with its measures. The handlers' own decisions on the labeled sets are never shown."""
        report = app_api.get(runtime.context.token, f"/api/evaluations/{run_id}")
        report.pop("claims", None)  # each claim sits beside the handler's decision: the Copilot never sees them
        return report

    @tool
    @refusal_safe
    def get_settings(runtime: Runtime) -> Any:
        """Get our settings that matter here: the approval limit, our objectives, and the number of claim agents a start uses."""
        rows = {r["name"]: r["value"] for r in app_api.get(runtime.context.token, "/api/settings")["settings"]}
        return {name: rows[name] for name in ("approval_limit", "objectives", "agents_at_once")}

    @tool
    @refusal_safe
    def start_dispatch(runtime: Runtime, workers: int | None = None) -> Any:
        """Start a dispatch of the whole queue with this many claim agents at once, or the number in our settings when the handler does not say. Answers with the run's id."""
        token = runtime.context.token
        return app_api.post(token, "/api/dispatches", {"workers": workers or agents_at_once(token)})

    @tool
    @refusal_safe
    def start_evaluation(runtime: Runtime, eval_set: Literal["evaluation", "held_out"], tiny: bool = False, workers: int | None = None) -> Any:
        """Start an evaluation on a labeled set, whole or only its five tiny claims, with this many claim agents at once, or the number in our settings. Answers with the run's id."""
        token = runtime.context.token
        return app_api.post(token, "/api/evaluations", {"eval_set": eval_set, "tiny": tiny, "workers": workers or agents_at_once(token)})

    @tool
    @refusal_safe
    def send_back(claim_id: str, note: str, runtime: Runtime) -> Any:
        """Send a held decision back to the agent with a note saying what to look at again."""
        return app_api.post(runtime.context.token, f"/api/claims/{claim_id}/send-back", {"note": note})

    @tool
    @refusal_safe
    def sign_off(claim_id: str, runtime: Runtime) -> Any:
        """Sign off a held payout, which pays it. The handler is first asked to confirm on a card: nothing is paid until they press Sign off and pay."""
        asker = runtime.context
        if not asker.handler.get("can_approve_over_limit"):  # checked here, so the card never shows for a handler who may not
            return f"{REFUSED}only a handler who can sign off payouts over the limit can sign off a payout"
        held = [p for p in app_api.get(asker.token, "/api/approvals") if p["claim_id"] == claim_id]
        if not held:
            return f"{REFUSED}no payout is held for a sign-off on {claim_id}"
        waiting = ToolMessage("Waiting for the handler to confirm on the card. Nothing is paid until they do.", name="sign_off",
                              tool_call_id=runtime.tool_call_id)
        return Command(update={"sign_off": {"claim_id": claim_id}, "messages": [waiting]})

    @tool
    @refusal_safe
    def wait_for_run(kind: Literal["dispatch", "evaluation"], run_id: int, runtime: Runtime) -> Any:
        """Wait for a dispatch or an evaluation you started to finish, then give how it went. It checks every few seconds without a model call, and after 15 minutes says it is still going and where to follow it."""
        return waiting.wait_for_run(runtime.context.token, kind, run_id)

    @tool
    @refusal_safe
    def what_needs_me(runtime: Runtime) -> Any:
        """List what needs a handler: the payouts held for sign-off, the claims referred or left for a handler, and the claims sent back that the agent has not worked yet."""
        token = runtime.context.token
        queue = app_api.get(token, "/api/claims")
        brief = lambda c: {k: c[k] for k in ("claim_id", "customer_name", "claim_type", "status", "decision", "payout")}  # noqa: E731
        return {"held_for_sign_off": app_api.get(token, "/api/approvals"),
                "referred_or_left": [brief(c) for c in queue if c["decision"] == "refer" or c["status"] == "needs_handler"],
                "sent_back_not_worked": [brief(c) for c in queue if c["status"] == "sent_back"]}

    @tool
    @refusal_safe
    def check_success_criteria(runtime: Runtime) -> Any:
        """Check the three success criteria we agreed: it evaluates the whole held-out set, waits for it, and works out each criterion's figure from our records. Set the rows out as a small table."""
        return criteria.check_criteria(runtime.context.token)

    own = [list_claims, get_claim, get_claim_steps, list_held_payouts, get_policy, find_customers, get_customer, get_latest_dispatch,
           list_evaluations, get_evaluation, get_settings, start_dispatch, start_evaluation, send_back, sign_off, wait_for_run, what_needs_me, check_success_criteria]
    return own + list(extra or []) + list(context_tools)
