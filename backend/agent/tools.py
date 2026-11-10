"""The claim agent's tools. The claim comes from the graph's state: the model never sees or chooses it.

The app's tools (the claim, its policy, its customer's history, the payout calculation and the decision) and the context
layer's tools (definitions, plans, rules, reference data and the documents) are thin layers over `route`, which is either
direct or the Gateway's. A context tool's answer names the file it came from: that goes back to the model in the answer and
into the claim's steps as the tool's artifact."""
import json
from typing import Annotated

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import BaseModel

from agent import memory as memory_of
from agent.route import Route

# Each section read goes back to the model on every later step, so reading costs grow fast.
MAX_SECTIONS = 8
DECISIONS = ("approve", "decline", "refer")
FOUND_CHARS = 200  # how much of what a lookup found its step keeps

# Facts every claim needs. They are handed to the agent as the results of tool calls it did not have to
# make, which saves a model round trip each and keeps customer text inside tool results.
PREFETCHED = ("get_claim", "get_policy", "get_plan", "get_claim_history")
NOT_OFFERED = (*PREFETCHED,)

ClaimId = Annotated[str, InjectedState("claim_id")]
Messages = Annotated[list, InjectedState("messages")]


class LineItem(BaseModel):
    description: str
    amount: float
    item_limit: float | None


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


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= FOUND_CHARS else text[: FOUND_CHARS - 1] + "…"


def build_tools(route: Route, decided_by: str = "UnderCover Agent", model: str = "unknown", store=None) -> list[BaseTool]:
    """Tools over the app's operations and the context layer's tools, on whichever route is in use. None of them takes a
    path, and none pays. `store` is the memory, when it is on. `decided_by` and `model` are what the agent fills into the decision record: the model never sees them."""

    def lookup(name: str, args: dict, found) -> tuple[str, dict]:
        """A context tool's answer for the model, and what the claim's steps keep of it."""
        result = route.context(name, args)
        return json.dumps(result), {"sources": result["sources"], "found": _short(found(result["answer"]))}

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

    @tool(response_format="content_and_artifact")
    def lookup_term(term: str) -> tuple[str, dict]:
        """Look up a term the policy wording defines, with the sections it comes from."""
        return lookup("lookup_term", {"term": term}, lambda a: a.get("definition") or f"not defined; {len(a['terms'])} terms listed")

    @tool(response_format="content_and_artifact")
    def describe_fields(record: str) -> tuple[str, dict]:
        """Describe the fields of a claim, a policy or a previous_claim, as the data dictionary defines them."""
        return lookup("describe_fields", {"record": record}, lambda a: f"{len(a['fields'])} fields of a {record}")

    @tool(response_format="content_and_artifact")
    def get_plan(plan: str) -> tuple[str, dict]:
        """Get one plan's limits, waiting periods, deductible and add-ons: economy, premium or first."""
        return lookup("get_plan", {"plan": plan}, lambda a: f"{a['plan']['name']}: deductible ${a['plan']['deductible']:,.2f}")

    @tool(response_format="content_and_artifact")
    def get_claim_rules() -> tuple[str, dict]:
        """Get the rules claims operations applies to every claim."""
        return lookup("get_claim_rules", {}, lambda a: ", ".join(r["name"] for r in a["rules"]))

    @tool(response_format="content_and_artifact")
    def search_reference_data(query: str) -> tuple[str, dict]:
        """Find the outside facts (events, places, products) that contain every word of the query."""
        return lookup("search_reference_data", {"query": query}, lambda a: f"{len(a)} facts")

    @tool(response_format="content_and_artifact")
    def search_documents(query: str) -> tuple[str, dict]:
        """Find the sections of the policy wording and the claims manual that mention a word or phrase."""
        return lookup("search_documents", {"query": query}, lambda a: f"{len(a)} sections")

    @tool(response_format="content_and_artifact")
    def read_section(document: str, section: int, messages: Messages) -> tuple[str, dict | None]:
        """Read one numbered section of 'policy_wording' or 'claims_manual'."""
        read = sections_read(messages)
        if (document, section) in read:
            return f"You already read {document} section {section} above. Use that text.", None
        if len(read) >= MAX_SECTIONS:
            return f"Error: you have read {MAX_SECTIONS} sections, the most for one claim. Decide with what you have.", None
        result = route.context("read_section", {"document": document, "number": section})
        source = result["sources"][0]
        text = f"{result['answer']['text']}\n\n[From {source['file']}: {source['system']}, {source['owner']}, reviewed {source['reviewed']}]"
        return text, {"sources": result["sources"], "found": result["answer"]["title"]}

    @tool(response_format="content_and_artifact")
    def search_memory(query: str, claim_id: ClaimId) -> tuple[str, dict]:
        """Search what we remember about this claim's customer from their earlier claims."""
        customer = route.app("get_claim", {"claim_id": claim_id})["claim"]["customer_id"]
        found = memory_of.search(store, customer, claim_id, query)
        return json.dumps(found), {"memory_query": query, "memory_found": found}

    @tool
    def calculate_payout(
        items: list[LineItem],
        claim_id: ClaimId,
        third_party_paid: float,
        deductible: float,
        benefit_max: float | None,
    ) -> str:
        """Work out the payout (wording section 10). Give every input: each item with the per-item limit
        that applies to it (null when none), 0 where nothing was paid or no deductible applies, and the
        benefit maximum (null when none)."""
        return json.dumps(route.app("calculate_payout", {
            "claim_id": claim_id,
            "items": [i.model_dump() if isinstance(i, BaseModel) else dict(i) for i in items],
            "third_party_paid": third_party_paid, "deductible": deductible, "benefit_max": benefit_max,
        }))

    @tool
    def record_decision(
        decision: str,
        payout: float,
        rationale: str,
        citations: list[str],
        claim_id: ClaimId,
        proposal: Annotated[dict | None, InjectedState("proposal")],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str | Command:
        """Record your decision on this claim. Call it once, when you are done.

        decision is approve, decline or refer. payout is in US dollars and must be 0 unless you approve; an approval
        needs a calculate_payout result for this claim, and its payout must be that result's payout. rationale is two
        or three short sentences naming the section of the wording relied on. citations are the sections you relied on."""
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
            "rationale": rationale.strip(),
            "citations": list(citations),
        }
        # the app checks the record against claims manual section 14: a refusal goes back to the model, which can put it right
        route.app("record_decision", {"claim_id": claim_id, **saved, "decided_by": decided_by, "model": model})
        return Command(update={
            "proposal": saved,
            "messages": [ToolMessage(f"Decision recorded for {claim_id}.", name="record_decision", tool_call_id=tool_call_id)],
        })

    every = [
        get_claim, get_policy, get_claim_history, get_plan, lookup_term, describe_fields, get_claim_rules,
        search_reference_data, search_documents, read_section, calculate_payout, record_decision,
    ]
    return [*every, search_memory] if store is not None else every
