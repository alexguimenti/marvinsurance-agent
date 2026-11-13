"""A deterministic stand-in for the Copilot's model, used by the end-to-end runs (AGENT_PROVIDER=scripted). It never calls a model; the
real graph, tools, cards and pause run around it.

A message that asks for a sign-off lists the held payouts and signs off the first. Any other message looks up the queue, starts a
dispatch of 2 claim agents, asks for a claim that does not exist (which the app refuses) and answers in markdown."""
import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agent.copilot.graph import CONFIRMATION

ANSWER = """Here is where things stand.

- The queue was looked up.
- A dispatch of 2 claim agents was started.
- `CLM-0000` is not in our records.

| Claim | Status |
|---|---|
| CLM-1001 | waiting |
"""


def _turn(messages: list) -> list:
    """The messages of the turn now going: from the handler's last message, leaving out a confirmation's."""
    start = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage) and m.name != CONFIRMATION)
    return messages[start:]


class ScriptedCopilotModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "scripted-copilot"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        turn = _turn(messages)
        asked = str(turn[0].content).lower()
        done = [m for m in turn if isinstance(m, ToolMessage)]
        if "sign off" in asked:
            held = next((m for m in done if m.name == "list_held_payouts"), None)
            if held is None:
                reply = self._call("list_held_payouts", {}, "held")
            elif not any(m.name == "sign_off" for m in done) and not any(
                    isinstance(m, HumanMessage) and m.name == CONFIRMATION for m in messages):
                payouts = json.loads(held.content)
                reply = self._call("sign_off", {"claim_id": payouts[0]["claim_id"]}, "sign") if payouts else AIMessage("Nothing is held for a sign-off.")
            else:
                said = next((m.content for m in reversed(messages) if isinstance(m, HumanMessage) and m.name == CONFIRMATION), "")
                reply = AIMessage(said or "Waiting for your confirmation.")
        elif not done:
            reply = self._call("list_claims", {}, "claims")
        elif len(done) == 1:
            reply = self._call("start_dispatch", {"workers": 2}, "dispatch")
        elif len(done) == 2:
            reply = self._call("get_claim", {"claim_id": "CLM-0000"}, "missing")
        else:
            reply = AIMessage(ANSWER)
        return ChatResult(generations=[ChatGeneration(message=reply)])

    @staticmethod
    def _call(name: str, args: dict, call_id: str) -> AIMessage:
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"scripted-{call_id}"}])
