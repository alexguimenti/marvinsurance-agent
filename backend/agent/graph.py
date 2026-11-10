"""The claim agent: a hand-written graph with its own state, nodes and edges.

Nodes record every step on the claim and hand refusals back to the model. The graph pays every approval, and
asks the backend whether a sign-off is needed: it never applies the limit itself.

    screen -> prepare -> model <-> tools -> tools_done -> decide -> pay

The security check comes first, outside the versioned configuration: a claim it flags is referred and never reaches the
claim agent's model.
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

from agent import api
from agent.connection import Refused
from agent.events import Usage, call_usage, emit_message, rationale_warnings
from agent.prompts import claim_prompt
from agent.screening import screen_claim
from agent.tools import NOT_OFFERED, PREFETCHED, build_tools, claim_data
from app.reference import Reference


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


def build_claim_graph(ref: Reference, model, model_id: str, max_model_calls: int, checkpointer, instructions: str,
                      screen_model, screen_model_id: str):
    tools = build_tools()
    by_name = {t.name: t for t in tools}
    offered = [t for t in tools if t.name not in NOT_OFFERED]
    # One tool call per turn: two save_decision calls in the same step would both write the proposal
    bound = model.bind_tools(offered, parallel_tool_calls=False)

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
        referral = {"decision": "refer", "payout": 0, "calculation": [], "rationale": screening.reason,
                    "citations": ["Security check"]}
        api.record_decision(claim_id, referral, screen_model_id)
        trace("decision", referral)
        return {"decision": referral}

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
        reply = bound.invoke([SystemMessage(claim_prompt(ref, instructions)), *state.messages])
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
            emit_message(message, recorder(state.claim_id), calls)
        return {}

    def decide(state: ClaimState) -> dict:
        """Have the backend record the decision. A repeat after a restart counts as done."""
        if state.proposal is None:
            raise ValueError("the agent finished without a decision")
        trace = recorder(state.claim_id)
        api.record_decision(state.claim_id, state.proposal, model_id)
        for warning in rationale_warnings(state.proposal["rationale"]):
            trace("warning", {"message": warning})
        trace("decision", state.proposal)
        return {"decision": state.proposal}

    def pay_node(state: ClaimState) -> dict:
        """Pay the approval, straight away or after a sign-off. The backend says whether one is needed, and refuses
        any payment over the limit without it, so the node never applies the limit itself. A sign-off pays the claim
        in the backend: after a wait, a paid claim ends the node with no call. The node runs again from the top on
        resume, so an early resume finds the claim still unsigned and waits again. Waiting and paying are one step on
        purpose: saving a step in between would be several more trips to the backend for a handler to wait through."""
        claim_id = state.claim_id
        current = api.claim_state(claim_id)
        if (current["payment"] or {}).get("status") == "paid":
            return {}
        if not current["needs_sign_off"]:
            try:
                paid_now = api.pay_claim(claim_id, state.decision["payout"])["paid_now"]
            except Refused as exc:
                if exc.status_code != 403:  # over the limit, as the limit stands now: a handler decides
                    raise
            else:
                if paid_now:
                    payment = api.claim_state(claim_id)["payment"]
                    recorder(claim_id)("status", {"status": "paid", "amount": payment["amount"],
                                                   "approved_by": payment["approved_by"]})
                return {}
        if api.hold(claim_id):
            recorder(claim_id)("status", {"status": "awaiting_approval"})
        while (api.claim_state(claim_id)["payment"] or {}).get("status") != "paid":
            interrupt({"claim_id": claim_id, "payout": state.decision["payout"], "waiting_for": "sign-off"})
        return {}

    def after_model(state: ClaimState) -> str:
        return "tools" if state.messages[-1].tool_calls else "decide"

    def after_tools(state: ClaimState) -> str:
        return "decide" if state.proposal is not None else "model"

    def after_decide(state: ClaimState) -> str:
        return "pay" if state.decision["decision"] == "approve" else END

    builder = StateGraph(ClaimState)
    builder.add_node("screen", screen)
    builder.add_node("prepare", prepare)
    builder.add_node("model", model_node)
    builder.add_node("tools", ToolNode(offered, handle_tool_errors=_tool_refused))
    builder.add_node("tools_done", tools_done)
    builder.add_node("decide", decide)
    builder.add_node("pay", pay_node)
    builder.add_edge(START, "screen")
    builder.add_conditional_edges("screen", lambda state: END if state.decision else "prepare", ["prepare", END])
    builder.add_edge("prepare", "model")
    builder.add_conditional_edges("model", after_model, ["tools", "decide"])
    builder.add_edge("tools", "tools_done")
    builder.add_conditional_edges("tools_done", after_tools, ["model", "decide"])
    builder.add_conditional_edges("decide", after_decide, ["pay", END])
    builder.add_edge("pay", END)
    return builder.compile(checkpointer=checkpointer)
