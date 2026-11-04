"""The claim agent: a hand-written graph with its own state, nodes and edges.

Nodes record every step on the claim and hand refusals back to the model. The graph pays every approval, and
asks the backend whether a sign-off is needed: it never applies the limit itself.

    prepare -> model <-> tools -> tools_done -> decide -> pay
"""
import time
from typing import Annotated

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from agent.events import Usage, emit_message, rationale_warnings
from agent.prompts import claim_prompt
from agent.tools import NOT_OFFERED, PREFETCHED, build_tools
from app.claims import hold_for_sign_off, needs_sign_off, record_decision
from app.payments import get_payment, pay
from app.reference import Reference
from app.runs import add_trace_event


class TooManyModelCalls(Exception):
    """The claim used up its model calls without a decision."""


class ClaimState(BaseModel):
    claim_id: str
    messages: Annotated[list, add_messages] = Field(default_factory=list)  # add_messages appends instead of replacing
    proposal: dict | None = None  # what save_decision wrote, before the backend records it
    decision: dict | None = None
    model_calls: int = 0
    usage: dict = Field(default_factory=dict)


def _tool_refused(error: Exception) -> str:
    return f"Error: {error}"


def build_claim_graph(engine: Engine, ref: Reference, model, model_id: str, max_model_calls: int, checkpointer):
    tools = build_tools(engine, ref)
    by_name = {t.name: t for t in tools}
    offered = [t for t in tools if t.name not in NOT_OFFERED]
    # One tool call per turn: two save_decision calls in the same step would both write the proposal
    bound = model.bind_tools(offered, parallel_tool_calls=False)

    def recorder(claim_id: str):
        return lambda kind, content: add_trace_event(engine, claim_id, kind, content)

    def prepare(state: ClaimState) -> dict:
        """Open the conversation with the facts every claim needs, as tool results it did not have to ask for."""

        def fetch(name: str) -> str:
            try:
                return by_name[name].invoke({"claim_id": state.claim_id})
            except LookupError as exc:  # a fact that is missing is reported, as a tool call would report it
                return f"Error: {exc}"

        call = AIMessage(content="", tool_calls=[{"name": n, "args": {}, "id": f"prefetch-{n}"} for n in PREFETCHED])
        results = [ToolMessage(fetch(n), name=n, tool_call_id=f"prefetch-{n}") for n in PREFETCHED]
        for message in (call, *results):
            emit_message(message, recorder(state.claim_id), {})
        return {"messages": [HumanMessage(f"Decide claim {state.claim_id}."), call, *results]}

    def model_node(state: ClaimState, config: RunnableConfig) -> dict:
        """One model turn."""
        if state.model_calls >= max_model_calls:
            raise TooManyModelCalls(f"the agent made {max_model_calls} model calls without a decision")
        deadline = config.get("configurable", {}).get("deadline")
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("the agent took too long on this claim")
        reply = bound.invoke([SystemMessage(claim_prompt(ref)), *state.messages])
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
            emit_message(message, recorder(state.claim_id), calls)
        return {}

    def decide(state: ClaimState) -> dict:
        """Have the backend record the decision. A repeat after a restart counts as done."""
        if state.proposal is None:
            raise ValueError("the agent finished without a decision")
        trace = recorder(state.claim_id)
        record_decision(engine, ref, state.claim_id, state.proposal, model_id)
        for warning in rationale_warnings(state.proposal["rationale"]):
            trace("warning", {"message": warning})
        trace("decision", state.proposal)
        if state.usage:
            trace("usage", state.usage)
        return {"decision": state.proposal}

    def pay_node(state: ClaimState) -> dict:
        """Pay the approval, straight away or after a sign-off. The backend says whether one is needed, refuses
        anything over the limit without it, and pays only once. The node runs again from the top on resume, so an
        early resume finds the claim still unsigned and waits again. Waiting and paying are one step on purpose:
        saving a step in between would be several more trips to the database for a handler to wait through."""
        needed = needs_sign_off(engine, ref, state.claim_id)
        if needed:
            first_time = get_payment(engine, state.claim_id) is None
            hold_for_sign_off(engine, ref, state.claim_id)
            if first_time:
                recorder(state.claim_id)("status", {"status": "awaiting_approval"})
        while needed:
            interrupt({"claim_id": state.claim_id, "payout": state.decision["payout"], "waiting_for": "sign-off"})
            needed = needs_sign_off(engine, ref, state.claim_id)
        if pay(engine, ref, state.claim_id)["paid_now"]:
            payment = get_payment(engine, state.claim_id)
            recorder(state.claim_id)("status", {
                "status": "paid", "amount": payment.amount, "approved_by": payment.approved_by,
            })
        return {}

    def after_model(state: ClaimState) -> str:
        return "tools" if state.messages[-1].tool_calls else "decide"

    def after_tools(state: ClaimState) -> str:
        return "decide" if state.proposal is not None else "model"

    def after_decide(state: ClaimState) -> str:
        return "pay" if state.decision["decision"] == "approve" else END

    builder = StateGraph(ClaimState)
    builder.add_node("prepare", prepare)
    builder.add_node("model", model_node)
    builder.add_node("tools", ToolNode(offered, handle_tool_errors=_tool_refused))
    builder.add_node("tools_done", tools_done)
    builder.add_node("decide", decide)
    builder.add_node("pay", pay_node)
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "model")
    builder.add_conditional_edges("model", after_model, ["tools", "decide"])
    builder.add_edge("tools", "tools_done")
    builder.add_conditional_edges("tools_done", after_tools, ["model", "decide"])
    builder.add_conditional_edges("decide", after_decide, ["pay", END])
    builder.add_edge("pay", END)
    return builder.compile(checkpointer=checkpointer)
