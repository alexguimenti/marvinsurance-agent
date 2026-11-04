"""Deterministic stand-ins for the model, used by the e2e runs (AGENT_PROVIDER=scripted).
They never call a model, and fail on purpose on a few claims so recovery can be exercised."""
import json
import re

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool

from agent.events import Emit

# Over-limit approvals, so the sign-off can be exercised.
_OVER_LIMIT = {"CLM-1003": 3750.0, "CLM-1008": 2240.0, "CLM-1011": 1150.0}
# Failures, so recovery can be exercised end to end.
_ALWAYS_FAILS = {"CLM-1013"}
_FAILS_ONCE = {"CLM-1017"}
_APPROVE = "The loss is covered. Section 10 of the policy wording sets the payment."
_DECLINE = "The claim is excluded. Section 9 of the policy wording does not pay it."
_REFER = "A document is missing. Section 11 of the policy wording says the claim waits."


def _decision(claim_id: str, amount_claimed: float) -> dict:
    if claim_id in _OVER_LIMIT:
        return _approval(_OVER_LIMIT[claim_id])
    if claim_id.endswith("5"):
        return {"decision": "decline", "payout": 0, "calculation": [], "rationale": _DECLINE,
                "citations": ["Policy wording, section 9"]}
    if claim_id.endswith("9"):
        return {"decision": "refer", "payout": 0, "calculation": [], "rationale": _REFER,
                "citations": ["Policy wording, section 11"]}
    return _approval(round(min(amount_claimed * 0.5, 900.0), 2))


def _approval(payout: float) -> dict:
    return {"decision": "approve", "payout": payout, "calculation": [{"step": "Eligible amount", "amount": payout}],
            "rationale": _APPROVE, "citations": ["Policy wording, section 10"]}


class ScriptedChatModel(BaseChatModel):
    """Reads section 10 of the wording, then decides. The real graph runs around it."""

    tried: set = set()  # claims that have used up their one scripted failure

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        claim_id = re.search(r"Decide claim (\S+)\.", next(m.content for m in messages if m.type == "human"))[1]
        if claim_id in _ALWAYS_FAILS or (claim_id in _FAILS_ONCE and claim_id not in self.tried):
            self.tried.add(claim_id)
            raise RuntimeError("scripted failure")
        if not any(m.type == "tool" and m.name == "read_section" for m in messages):
            call = {"name": "read_section", "args": {"document": "policy_wording", "section": 10}, "id": f"read-{claim_id}"}
        else:
            claim_text = next(m.content for m in messages if m.type == "tool" and m.name == "get_claim")
            claim = json.loads(claim_text.removeprefix("<claim_data>").rstrip().removesuffix("</claim_data>"))
            call = {"name": "save_decision", "args": _decision(claim_id, claim["amount_claimed"]), "id": f"save-{claim_id}"}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])


class ScriptedQueueAgent:
    """Works claims in id order. Never calls a model."""

    model_id = "scripted"
    usage = None

    def run(self, tools: list[BaseTool], on_event: Emit, resuming: bool = False) -> None:
        by_name = {t.name: t for t in tools}

        def call(name: str, args: dict) -> str:
            on_event("tool_call", {"name": name, "args": args})
            result = by_name[name].invoke(args)
            on_event("tool_result", {"name": name, "content": result})
            return result

        listed = json.loads(call("list_waiting_claims", {}))
        room = None if listed["cap"] is None else listed["cap"] - listed["taken"]
        plan = [c["claim_id"] for c in listed["claims"]][:room]
        call("set_todo", {"items": [{"title": f"Work {cid}", "claim_id": cid} for cid in plan]})
        reports = [call("work_claim", {"claim_id": cid}) for cid in plan]
        for _ in range(3):  # claims that came back to the queue are worked again after the others
            answer = call("finish_run", {"summary": self._summary(reports)})
            if not answer.startswith("Not finished"):
                return
            owed = answer.split(": ", 1)[1].split(", ")
            reports += [call("work_claim", {"claim_id": cid}) for cid in owed]

    @staticmethod
    def _summary(reports: list[str]) -> str:
        latest = {line.split(":", 1)[0]: line for line in reports}
        return f"Worked {len(latest)} claims.\n" + "\n".join(latest.values())
