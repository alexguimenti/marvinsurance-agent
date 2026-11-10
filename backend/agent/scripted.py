"""Deterministic stand-ins for the model, used by the e2e runs (AGENT_PROVIDER=scripted).
They never call a model, and fail on purpose on a few claims so recovery can be exercised."""
import json
import re

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda

from agent.screening import Screening

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
        original = re.sub(r"^E\d+-", "", claim_id)  # an evaluation's copy behaves as the claim it copies
        if original in _ALWAYS_FAILS or (original in _FAILS_ONCE and claim_id not in self.tried):
            self.tried.add(claim_id)
            raise RuntimeError("scripted failure")
        if not any(m.type == "tool" and m.name == "read_section" for m in messages):
            call = {"name": "read_section", "args": {"document": "policy_wording", "section": 10}, "id": f"read-{claim_id}"}
        else:
            claim_text = next(m.content for m in messages if m.type == "tool" and m.name == "get_claim")
            claim = json.loads(claim_text.removeprefix("<claim_data>").rstrip().removesuffix("</claim_data>"))
            call = {"name": "save_decision", "args": _decision(original, claim["amount_claimed"]), "id": f"save-{claim_id}"}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])


class ScriptedImproverModel(BaseChatModel):
    """The improver without a model, for the end-to-end runs: it reads the results, proposes the current instructions with
    one added rule, has the version evaluated, then proposes the recipe with one added step. A proposal that comes back
    rejected is proposed again with a different rule."""

    @property
    def _llm_type(self) -> str:
        return "scripted-improver"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        tool_results = [m for m in messages if m.type == "tool"]
        attempts = sum(1 for m in tool_results if getattr(m, "status", None) == "error")
        last = tool_results[-1] if tool_results else None
        saved = last is not None and str(last.content).startswith("Saved as")
        step = f"{attempts + 1}"
        if last is None:
            call = {"name": "read_results", "args": {}, "id": "read"}
        elif last.name == "read_results" or (last.name == "propose_change" and not saved):
            view = next(json.loads(m.content) for m in tool_results if m.name == "read_results")
            current = view["current_version"]
            call = {"name": "propose_change", "id": f"change-{step}", "args": {
                "instructions": current["instructions"].rstrip("\n") + f"\n- When a receipt is missing, refer the claim instead of approving it (rule {step}).",
                "model": current["model"], "reasoning_effort": current["reasoning_effort"],
                "reason": "The misses are approvals with documents missing."}}
        elif last.name == "propose_change":
            call = {"name": "run_evaluation", "args": {}, "id": "evaluate"}
        elif last.name == "run_evaluation" or (last.name == "propose_recipe" and not saved):
            system = next(m.content for m in messages if m.type == "system")
            recipe = system.split("# Your recipe", 1)[1].strip()
            call = {"name": "propose_recipe", "id": f"recipe-{step}", "args": {
                "recipe": recipe + f"\n6. Read the earlier rounds before you start (step {step}).",
                "reason": "This round showed that earlier rounds should be read first."}}
        else:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="The round is done."))])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])


class ScriptedScreenModel:
    """The security check without a model, for the end-to-end runs: it flags a claim whose text holds `PROCESSING NOTE`,
    the way the security team's claim does. It is read as the real check is, through structured output."""

    def with_structured_output(self, schema, **kwargs):
        def answer(messages):
            text = str(messages[-1].content)
            found = "PROCESSING NOTE" in text
            parsed = Screening(reason="The claim holds a processing note that instructs its handler." if found
                               else "The customer describes what happened.", instructions_found=found)
            return {"raw": AIMessage(content=parsed.model_dump_json()), "parsed": parsed, "parsing_error": None}

        return RunnableLambda(answer)
