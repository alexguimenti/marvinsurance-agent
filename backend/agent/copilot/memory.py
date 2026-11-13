"""The Copilot's memory of each handler, kept in AgentCore Memory (in our own AWS account), apart from the claim agent's memory of customers.

In this Memory the actor is the handler and the session is the conversation. Each turn's two messages go in as events, the handler's and
the Copilot's answer, and AgentCore turns them into notes in about a minute and a half: the handler's preferences under
`/handlers/{actorId}/preferences/` and the facts they tell it under `/handlers/{actorId}/facts/`. So a preference shows in the handler's
next conversation rather than in the same turn. At the start of a turn the Copilot searches the notes of the signed-in handler, and
nobody else's. Only the message and the answer are saved: never a tool call, a tool result or the sign-in.

The handler is whoever the app says is signed in, from the graph's context, never a name from the model or the message. Memory is an aid:
a lookup or a save that fails is logged and the turn goes on as it would without memory."""
import logging
import os

import boto3
from langchain_core.messages import AIMessage, HumanMessage
from langgraph_checkpoint_aws import AgentCoreMemoryStore

from agent.copilot import app_api
from agent.copilot.asker import Asker

log = logging.getLogger(__name__)
SEARCH_LIMIT = 5
NOTHING = "Nothing remembered about this handler yet."


def open_copilot_store():
    """The Copilot's AgentCore Memory in UNDERCOVER_COPILOT_MEMORY_ID, in the AWS CLI's region, or None when memory is off. Read each
    time it is called, never at import."""
    memory_id = os.environ.get("UNDERCOVER_COPILOT_MEMORY_ID")
    if not memory_id:
        return None
    return AgentCoreMemoryStore(memory_id=memory_id, region_name=boto3.Session().region_name)


def recall(store, asker: Asker, message: str) -> list[str]:
    """What is remembered about the signed-in handler that bears on their message, shown in the sidebar as a card like any other."""
    handler_id = asker.handler["handler_id"]
    step = app_api.record_step(asker.token, asker.turn_id, "memory", "search_memory", message[:80], {"query": message})
    try:
        found = store.search(("handlers", handler_id), query=message, limit=SEARCH_LIMIT)
        notes = [item.value["content"] for item in found]
    except Exception as exc:
        log.warning("the Copilot's memory could not be searched: %s", type(exc).__name__)
        app_api.finish_step(asker.token, step, "failed", None, None, f"{type(exc).__name__}: {exc}"[:300])
        return []
    app_api.finish_step(asker.token, step, "done", "\n".join(notes) if notes else NOTHING, None, None)
    return notes


def save_turn(store, asker: Asker, message: str, answer: str) -> None:
    """The handler's message and the Copilot's answer, as two events of the handler's, in the conversation's session."""
    namespace = (asker.handler["handler_id"], asker.conversation_id)
    try:
        store.put(namespace, f"turn-{asker.turn_id}-message", {"message": HumanMessage(message)})
        store.put(namespace, f"turn-{asker.turn_id}-answer", {"message": AIMessage(answer)})
    except Exception as exc:
        log.warning("the Copilot's memory could not be saved to: %s", type(exc).__name__)
