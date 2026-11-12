"""The claim agent: a hand-written graph with its own state, nodes and edges.

Nodes record every step on the claim and hand refusals back to the model. The graph pays every approval, and
asks the backend whether a sign-off is needed: it never applies the limit itself.

    screen -> prepare -> model <-> tools -> tools_done -> decide -> pay -> (wait_for_sign_off) -> remember

The security check comes first, outside the versioned configuration: a claim it flags is referred and never reaches the
claim agent's model.
"""
import json
import logging
import time
from typing import Annotated

import httpx

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from agent import api
from agent import memory as memory_of
from agent.connection import Refused
from agent.events import GATEWAY_REFUSAL, Usage, call_usage, emit_message, rationale_warnings
from agent.gateway import GatewayRefused
from agent.mcp_bridge import ToolRefused
from agent.prompts import LABELS, claim_prompt, section_index
from agent.route import Route
from agent.screening import screen_claim
from agent.tools import NOT_OFFERED, PREFETCHED, build_tools, claim_data
from app.reference import Reference


log = logging.getLogger(__name__)


class TooManyModelCalls(Exception):
    """The claim used up its model calls without a decision."""


class ClaimState(BaseModel):
    claim_id: str
    messages: Annotated[list, add_messages] = Field(default_factory=list)  # add_messages appends instead of replacing
    proposal: dict | None = None  # what save_decision wrote, before the backend records it
    decision: dict | None = None
    model_calls: int = 0
    usage: dict = Field(default_factory=dict)
    settled: str | None = None  # where the payment stands: paid, held (waiting for a sign-off) or copy_held


def _tool_refused(error: Exception) -> str:
    """What the model is shown when a tool is refused: the reason, and whose refusal it was when it is the Gateway's. A
    transport error is nobody's refusal (the backend, the Gateway or the context layer is out of reach): it goes up, and the
    claim carries on from its last saved step."""
    if isinstance(error, httpx.TransportError):
        raise error
    return f"{GATEWAY_REFUSAL}{error}" if isinstance(error, GatewayRefused) else f"Error: {error}"


def _needs_a_handler(exc: Exception) -> bool:
    """The app refused a payment over the limit as the limit stands now, or one that needs a sign-off."""
    if isinstance(exc, Refused):
        return exc.status_code == 403
    return isinstance(exc, ToolRefused) and ("approval limit" in str(exc) or "sign-off" in str(exc))


def build_claim_graph(ref: Reference, model, model_id: str, max_model_calls: int, checkpointer, instructions: str,
                      screen_model, screen_model_id: str, route: Route, decided_by: str, store=None):
    tools = build_tools(route, decided_by, model_id, store)
    by_name = {t.name: t for t in tools}
    offered = [t for t in tools if t.name not in NOT_OFFERED]
    # One tool call per turn: two save_decision calls in the same step would both write the proposal
    bound = model.bind_tools(offered, parallel_tool_calls=False)

    sections: list[str] = []  # the documents' index, asked for once for this graph

    def index() -> str:
        if not sections:
            sections.append(section_index({document: api.list_sections(document) for document in LABELS}))
        return sections[0]

    def recorder(claim_id: str):
        return lambda kind, content: api.add_step(claim_id, kind, content)

    def screen(state: ClaimState, config: RunnableConfig) -> dict:
        """Have a second model read the claim before the claim agent's model does. A claim that tries to instruct its
        handler is referred with the check's reason, and goes no further. An error fails the claim as any other does."""
        claim_id = state.claim_id
        run_id = config.get("configurable", {}).get("run_id")
        screening = screen_claim(screen_model, screen_model_id, claim_data(api.get_claim(claim_id)),
                                 lambda usage: api.record_model_call(claim_id, run_id, screen_model_id, usage))
        trace = recorder(claim_id)
        trace("screening", {"instructions_found": screening.instructions_found, "reason": screening.reason,
                            "model": screen_model_id})
        if not screening.instructions_found:
            return {}
        referral = {"decision": "refer", "payout": 0, "rationale": screening.reason, "citations": ["Security check"]}
        api.record_decision(claim_id, referral, screen_model_id, "Security check")
        trace("decision", referral)
        return {"decision": referral}

    def prepare(state: ClaimState) -> dict:
        """Open the conversation with the facts every claim needs, as tool results it did not have to ask for: the claim,
        its policy, the policy's plan from the context layer, and the customer's history."""
        calls, results, plan = [], [], None
        for name in PREFETCHED:
            args = {"plan": plan} if name == "get_plan" else {}
            call_id = f"prefetch-{name}"
            calls.append({"name": name, "args": args, "id": call_id})
            if name == "get_plan" and plan is None:
                results.append(ToolMessage("Error: the policy has no plan to look up", name=name, tool_call_id=call_id, status="error"))
                continue
            try:
                answer = by_name[name].invoke({"type": "tool_call", "name": name, "id": call_id, "args": {**args, "claim_id": state.claim_id}
                                               if name != "get_plan" else args})
            except (LookupError, ToolRefused, GatewayRefused, Refused) as exc:  # a fact that is missing is reported as a refusal
                answer = ToolMessage(_tool_refused(exc), name=name, tool_call_id=call_id, status="error")
            if name == "get_policy" and answer.status != "error":
                plan = json.loads(str(answer.content))["plan"]
            results.append(answer)
        call = AIMessage(content="", tool_calls=calls)
        for message in (call, *results):
            emit_message(message, recorder(state.claim_id), {c["id"]: c["args"] for c in calls}, route.shown_name)
        return {"messages": [HumanMessage(f"Decide claim {state.claim_id}."), call, *results]}

    def model_node(state: ClaimState, config: RunnableConfig) -> dict:
        """One model turn."""
        if state.model_calls >= max_model_calls:
            raise TooManyModelCalls(f"the agent made {max_model_calls} model calls without a decision")
        deadline = config.get("configurable", {}).get("deadline")
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("the agent took too long on this claim")
        reply = bound.invoke([SystemMessage(claim_prompt(instructions, index())), *state.messages])
        run_id = config.get("configurable", {}).get("run_id")
        api.record_model_call(state.claim_id, run_id, model_id, call_usage(reply))
        usage = Usage(state.usage)
        usage.add(reply)
        emit_message(reply, recorder(state.claim_id), {})
        return {"messages": [reply], "model_calls": state.model_calls + 1, "usage": usage.as_dict()}

    def tools_done(state: ClaimState) -> dict:
        """Record what the tools answered. Their refusals are already on their way back to the model."""
        turn = []
        for message in reversed(state.messages):
            if not isinstance(message, ToolMessage):
                calls = {c["id"]: c["args"] for c in message.tool_calls}
                break
            turn.insert(0, message)
        for message in turn:
            emit_message(message, recorder(state.claim_id), calls, route.shown_name)
        return {}

    def decide(state: ClaimState) -> dict:
        """Trace the decision the record_decision tool made. The app already holds it, checked against claims manual section 14."""
        if state.proposal is None:
            raise ValueError("the agent finished without a decision")
        trace = recorder(state.claim_id)
        for warning in rationale_warnings(state.proposal["rationale"]):
            trace("warning", {"message": warning})
        trace("decision", state.proposal)
        return {"decision": state.proposal}

    def remember(state: ClaimState) -> dict:
        """Save the claim and its decision to memory, in code, every time: never at the model's choice. Nothing is saved when
        memory is off or for an evaluation's copy (a claim the security check referred never gets here). When AgentCore
        cannot be reached it logs a warning and the claim keeps its decision."""
        if store is None or api.claim_state(state.claim_id)["copy_of"] is not None:
            return {}
        trace = recorder(state.claim_id)
        try:
            saved = memory_of.save_claim(store, api.get_claim(state.claim_id), state.decision)
        except Exception as exc:
            log.warning("could not save %s to memory: %s: %s", state.claim_id, type(exc).__name__, exc)
            trace("memory_save", {"error": f"{type(exc).__name__}: {exc}"})
        else:
            trace("memory_save", {"saved": saved})
        return {}

    def pay_node(state: ClaimState) -> dict:
        """Pay the approval straight away, or hold it for a handler's sign-off. The backend says whether a sign-off is
        needed, and refuses any payment over the limit without it, so the node never applies the limit itself. A sign-off
        pays the claim in the backend, so a claim already paid ends the node with no call."""
        claim_id = state.claim_id
        trace = recorder(claim_id)
        current = api.claim_state(claim_id)
        if (current["payment"] or {}).get("status") == "paid":
            return {"settled": "paid"}
        if not current["needs_sign_off"]:
            try:
                paid_now = route.app("pay_claim", {"claim_id": claim_id, "amount": state.decision["payout"]})["paid_now"]
            except GatewayRefused as exc:  # the Policy refused it, which can happen only when the limit was raised above it
                trace("refusal", {"by": "gateway", "tool": route.shown_name("pay_claim"), "reason": str(exc)})
                if api.gateway_refused(claim_id):
                    trace("status", {"status": "awaiting_approval"})
            except (Refused, ToolRefused) as exc:
                if not _needs_a_handler(exc):  # over the limit, as the limit stands now: a handler decides
                    raise
            else:
                if paid_now:
                    payment = api.claim_state(claim_id)["payment"]
                    trace("status", {"status": "paid", "amount": payment["amount"], "approved_by": payment["approved_by"]})
                return {"settled": "paid"}
        if api.hold(claim_id):
            trace("status", {"status": "awaiting_approval"})
        # nobody signs off an evaluation's copy: its work ends here, held, and its saved progress goes
        return {"settled": "copy_held" if current["copy_of"] is not None else "held"}

    def wait_for_sign_off(state: ClaimState) -> dict:
        """Pause until a handler answers. The place is saved on the claim's thread. On a resume LangGraph runs this node
        again from the top, so nothing goes before the interrupt: the answer is read from the app's record. An early resume
        finds the claim still waiting, and pauses again."""
        interrupt({"claim_id": state.claim_id, "payout": state.decision["payout"], "waiting_for": "sign-off"})
        current = api.claim_state(state.claim_id)
        if (current["payment"] or {}).get("status") == "paid":
            return {"settled": "paid"}
        return {"settled": "held"}

    def after_model(state: ClaimState) -> str:
        return "tools" if state.messages[-1].tool_calls else "decide"

    def after_tools(state: ClaimState) -> str:
        return "decide" if state.proposal is not None else "model"

    def after_decide(state: ClaimState) -> str:
        return "pay" if state.decision["decision"] == "approve" else "remember"

    def after_pay(state: ClaimState) -> str:
        return {"paid": "remember", "held": "wait_for_sign_off"}.get(state.settled, END)

    def after_wait(state: ClaimState) -> str:
        return "remember" if state.settled == "paid" else "wait_for_sign_off"

    builder = StateGraph(ClaimState)
    builder.add_node("screen", screen)
    builder.add_node("prepare", prepare)
    builder.add_node("model", model_node)
    builder.add_node("tools", ToolNode(offered, handle_tool_errors=_tool_refused))
    builder.add_node("tools_done", tools_done)
    builder.add_node("decide", decide)
    builder.add_node("pay", pay_node)
    builder.add_node("wait_for_sign_off", wait_for_sign_off)
    builder.add_node("remember", remember)
    builder.add_edge(START, "screen")
    builder.add_conditional_edges("screen", lambda state: END if state.decision else "prepare", ["prepare", END])
    builder.add_edge("prepare", "model")
    builder.add_conditional_edges("model", after_model, ["tools", "decide"])
    builder.add_edge("tools", "tools_done")
    builder.add_conditional_edges("tools_done", after_tools, ["model", "decide"])
    builder.add_conditional_edges("decide", after_decide, ["pay", "remember"])
    builder.add_conditional_edges("pay", after_pay, ["remember", "wait_for_sign_off", END])
    builder.add_conditional_edges("wait_for_sign_off", after_wait, ["remember", "wait_for_sign_off"])
    builder.add_edge("remember", END)
    return builder.compile(checkpointer=checkpointer)
