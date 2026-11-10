"""The claim agent's tools. The claim comes from the graph's state: the model never sees or chooses it."""
import json
from typing import Annotated

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import BaseModel

from agent import api
from agent.route import Route
from app.documents import DOCUMENTS

# Each section read goes back to the model on every later step, so reading costs grow fast.
MAX_SECTIONS = 8
DECISIONS = ("approve", "decline", "refer")

# Facts every claim needs. They are handed to the agent as the results of tool calls it did not have to
# make, which saves a model round trip each and keeps customer text inside tool results.
PREFETCHED = ("get_claim", "get_policy", "get_plan", "get_claim_history")
NOT_OFFERED = (*PREFETCHED, "list_sections")

ClaimId = Annotated[str, InjectedState("claim_id")]
Messages = Annotated[list, InjectedState("messages")]


class LineItem(BaseModel):
    description: str
    amount: float
    item_limit: float | None


class CalcStep(BaseModel):
    step: str
    amount: float


def claim_data(claim: dict) -> str:
    """The claim as the model sees it, inside the tags that mark it as the customer's text."""
    return "<claim_data>\n" + json.dumps(claim) + "\n</claim_data>"


def sections_read(messages: list) -> set[tuple[str, int]]:
    """The sections already read in this conversation, so the rules hold after a restart as well."""
    args = {c["id"]: c["args"] for m in messages if isinstance(m, AIMessage) for c in m.tool_calls}
    return {
        (args[m.tool_call_id]["document"], args[m.tool_call_id]["section"])
        for m in messages
        if isinstance(m, ToolMessage) and m.name == "read_section" and m.tool_call_id in args
        and not str(m.content).startswith(("Error:", "You already read"))
    }


def build_tools(route: Route) -> list[BaseTool]:
    """Tools over the app's operations, on whichever route is in use. None of them takes a path, and none pays."""
    unknown = f"Documents: {', '.join(DOCUMENTS)}."

    @tool
    def get_claim(claim_id: ClaimId) -> str:
        """Get the claim: the customer's story, the items claimed and the supporting documents."""
        return claim_data(route.app("get_claim", {"claim_id": claim_id})["claim"])

    @tool
    def get_policy(claim_id: ClaimId) -> str:
        """Get the policy for the trip: plan, add-ons, purchase date, trip dates and travelers."""
        return json.dumps(route.app("get_policy", {"claim_id": claim_id}))

    @tool
    def get_claim_history(claim_id: ClaimId) -> str:
        """Get the customer's name, customer-since date and earlier claims."""
        return json.dumps(route.app("get_claim_history", {"claim_id": claim_id}))

    @tool
    def get_plan(claim_id: ClaimId) -> str:
        """Get the limits, waiting periods and deductible of the policy's plan, and its add-ons."""
        return json.dumps(api.get_plan(claim_id))

    @tool
    def list_sections(document: str) -> str:
        """List the numbered sections of a document: 'policy_wording' (the contract) or 'claims_manual'."""
        try:
            return json.dumps(api.list_sections(document))
        except LookupError as exc:
            return f"Error: {exc}. {unknown}"

    @tool
    def read_section(document: str, section: int, messages: Messages) -> str:
        """Read one numbered section of 'policy_wording' or 'claims_manual'."""
        read = sections_read(messages)
        if (document, section) in read:
            return f"You already read {document} section {section} above. Use that text."
        if len(read) >= MAX_SECTIONS:
            return f"Error: you have read {MAX_SECTIONS} sections, the most for one claim. Decide with what you have."
        try:
            return api.read_section(document, section)
        except LookupError as exc:
            return f"Error: {exc}. {unknown}"

    @tool
    def search_documents(query: str) -> str:
        """Find the sections of the policy wording and the claims manual that mention a word or phrase."""
        return json.dumps(api.search_documents(query))

    @tool
    def lookup_knowledge(topic: str) -> str:
        """Look up public facts (events, products, places). Give a topic such as 'events' or a search word."""
        term = topic.lower()
        facts = [f for f in api.knowledge() if f["topic"].lower() == term or term in f["fact"].lower()]
        return json.dumps(facts)

    @tool
    def calculate_payout(
        items: list[LineItem],
        third_party_paid: float,
        deductible: float,
        benefit_max: float | None,
    ) -> str:
        """Work out the payout (wording section 10). Give every input: each item with the per-item limit
        that applies to it (null when none), 0 where nothing was paid or no deductible applies, and the
        benefit maximum (null when none)."""
        return json.dumps(route.app("calculate_payout", {
            "items": [i.model_dump() if isinstance(i, BaseModel) else dict(i) for i in items],
            "third_party_paid": third_party_paid, "deductible": deductible, "benefit_max": benefit_max,
        }))

    @tool
    def save_decision(
        decision: str,
        payout: float,
        calculation: list[CalcStep],
        rationale: str,
        citations: list[str],
        claim_id: ClaimId,
        proposal: Annotated[dict | None, InjectedState("proposal")],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str | Command:
        """Save your decision on this claim. Call it once, when you are done.

        decision is approve, decline or refer. payout is in US dollars and must be 0 unless you approve.
        calculation holds the steps calculate_payout returned. rationale is two or three short sentences
        naming the section of the wording relied on. citations are the places you relied on."""
        if decision not in DECISIONS:
            return f"Error: decision must be one of {', '.join(DECISIONS)}."
        amount_claimed = route.app("get_claim", {"claim_id": claim_id})["claim"]["amount_claimed"]
        if decision == "approve" and not 0 < payout <= amount_claimed:
            return f"Error: an approval needs a payout above 0 and at most the amount claimed ({amount_claimed})."
        if decision != "approve" and payout != 0:
            return "Error: payout must be 0 unless the decision is approve."
        if not rationale.strip():
            return "Error: the rationale is empty."
        if proposal is not None:
            return f"Error: {claim_id} already has a decision."
        saved = {
            "decision": decision,
            "payout": round(float(payout), 2),
            "calculation": [s.model_dump() if isinstance(s, BaseModel) else dict(s) for s in calculation],
            "rationale": rationale.strip(),
            "citations": list(citations),
        }
        return Command(update={
            "proposal": saved,
            "messages": [ToolMessage(f"Decision saved for {claim_id}.", name="save_decision", tool_call_id=tool_call_id)],
        })

    return [
        get_claim, get_policy, get_claim_history, get_plan,
        list_sections, read_section, search_documents, lookup_knowledge, calculate_payout, save_decision,
    ]
