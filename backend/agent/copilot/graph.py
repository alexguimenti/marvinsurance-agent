"""The Copilot's graph, written by hand as the claim agent's is: a node that remembers, a node that calls the model, LangGraph's ToolNode
and the loop between them. A turn makes at most 25 model calls.

    START -> recall -> model -> (tools -> model ... | save -> END)
    tools -> confirm -> model        when a sign-off waits for the handler's confirmation

The handler's sign-in and who they are reach the nodes and the tools through the graph's context (`context_schema=Asker`), never the
config or the state, which LangGraph saves with each checkpoint. The system prompt is built for each call and is not part of the saved
messages."""
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from agent.copilot import app_api, memory
from agent.copilot.asker import Asker
from agent.connection import Refused
from agent.copilot.cards import carded
from agent.events import call_usage

INSTRUCTIONS = Path(__file__).with_name("instructions.md")
MAX_MODEL_CALLS = 25
CONFIRMATION = "confirmation"  # the name of the message that tells the model what the handler pressed


class TooManyModelCalls(Exception):
    """The model went past its calls for one turn without answering."""


class CopilotState(MessagesState):
    model_calls: int  # this turn's, reset when a turn starts
    cost_usd: float  # this turn's, as OpenRouter reports it
    notes: list  # what the Copilot remembers about this handler, found at the start of the turn
    sign_off: dict | None  # the claim a sign-off waits on, until the handler answers


def load_instructions() -> str:
    return INSTRUCTIONS.read_text(encoding="utf-8")


def system_prompt(instructions: str, handler: dict, notes: list | None) -> str:
    """The instructions, who the Copilot is working for, and what it remembers about them."""
    may = "may" if handler.get("can_approve_over_limit") else "may not"
    prompt = (f"{instructions}\n\n## Who is asking\n\n{handler['name']} ({handler['role']}), handler id {handler['handler_id']}. "
              f"They {may} sign off payouts over the approval limit.")
    if notes:
        prompt += f"\n\n## What you remember about {handler['name'].split()[0]}\n\n" + "\n".join(f"- {n}" for n in notes)
    return prompt


def turn_message(messages: list) -> str:
    """What the handler asked in the turn now going: the last of their messages, leaving out a confirmation's."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage) and message.name != CONFIRMATION:
            return str(message.content)
    return ""


def answer_of(messages: list) -> str:
    """The Copilot's answer: the text of its last message that is not a tool call."""
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls:
            return message.content if isinstance(message.content, str) else "".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in message.content)
    return ""


def build_copilot_graph(model, checkpointer, tools: list, store, instructions: str, max_model_calls: int = MAX_MODEL_CALLS):
    bound = model.bind_tools(tools, parallel_tool_calls=False)  # one call at a time: a sign-off must not share a step with another call

    def recall(state: CopilotState, runtime: Runtime[Asker]) -> dict:
        """What the Copilot remembers about this handler that bears on their message. Off when there is no memory."""
        if store is None:
            return {"notes": []}
        return {"notes": memory.recall(store, runtime.context, turn_message(state["messages"]))}

    def model_node(state: CopilotState, runtime: Runtime[Asker]) -> dict:
        calls = state.get("model_calls", 0)
        if calls >= max_model_calls:
            raise TooManyModelCalls(f"the Copilot made {max_model_calls} model calls without an answer")
        system = SystemMessage(system_prompt(instructions, runtime.context.handler, state.get("notes")))
        reply = bound.invoke([system, *state["messages"]])
        cost = call_usage(reply)["cost_usd"]
        return {"messages": [reply], "model_calls": calls + 1, "cost_usd": round(state.get("cost_usd", 0.0) + (cost or 0.0), 8)}

    def confirm(state: CopilotState, runtime: Runtime[Asker]) -> dict:
        """The pause before a sign-off, in a node of its own: LangGraph runs an interrupted node again from the top when it resumes, so
        nothing comes before the `interrupt()`. The handler's answer comes back as `Command(resume=...)`; "sign_off" pays, anything
        else pays nothing."""
        answer = interrupt(state["sign_off"])
        claim_id, asker = state["sign_off"]["claim_id"], runtime.context
        if answer != "sign_off":
            told = "The handler pressed Cancel. Nothing was signed off or paid."
        else:
            args = {"claim_id": claim_id}
            step = app_api.record_step(asker.token, asker.turn_id, "action", "sign_off", claim_id, args)
            try:
                paid = app_api.post(asker.token, f"/api/payments/PAY-{claim_id}/approve")["amount"]
            except (Refused, LookupError) as exc:
                app_api.finish_step(asker.token, step, "refused", None, None, str(exc)[:500])
                told = f"The handler pressed Sign off and pay, but the app refused it: {exc}. Nothing was paid."
            else:
                app_api.finish_step(asker.token, step, "done", f"Paid ${paid:,.2f} on {claim_id}.", None, None)
                told = f"The handler pressed Sign off and pay. {claim_id} was signed off and ${paid:,.2f} was paid."
        return {"sign_off": None, "messages": [HumanMessage(told, name=CONFIRMATION)]}

    def save(state: CopilotState, runtime: Runtime[Asker]) -> dict:
        """After the turn: the handler's message and the answer go to their memory, and nothing else."""
        if store is not None:
            memory.save_turn(store, runtime.context, turn_message(state["messages"]), answer_of(state["messages"]))
        return {}

    def after_model(state: CopilotState) -> str:
        return "tools" if state["messages"][-1].tool_calls else "save"

    def after_tools(state: CopilotState) -> str:
        return "confirm" if state.get("sign_off") else "model"

    builder = StateGraph(CopilotState, context_schema=Asker)
    builder.add_node("recall", recall)
    builder.add_node("model", model_node)
    builder.add_node("tools", ToolNode(tools, wrap_tool_call=carded))
    builder.add_node("confirm", confirm)
    builder.add_node("save", save)
    builder.add_edge(START, "recall")
    builder.add_edge("recall", "model")
    builder.add_conditional_edges("model", after_model, ["tools", "save"])
    builder.add_conditional_edges("tools", after_tools, ["confirm", "model"])
    builder.add_edge("confirm", "model")
    builder.add_edge("save", END)
    return builder.compile(checkpointer=checkpointer)
