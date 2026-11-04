import json
import sqlite3
from dataclasses import asdict

from langchain_core.tools import BaseTool, tool

from app.documents import DOCUMENTS, documents_for
from app.payments import request_payment as create_payment
from app.payout import LineItem, calculate_payout as compute_payout
from app.reference import Reference

# Each section read goes back to the model on every later step, so reading costs grow fast.
MAX_SECTIONS = 8


def build_tools(conn: sqlite3.Connection, ref: Reference, claim_id: str) -> list[BaseTool]:
    """Read-only tools bound to a single claim. None of them takes a path or a claim id."""
    docs = documents_for(ref)
    unknown = f"Documents: {', '.join(DOCUMENTS)}."

    def claim() -> dict:
        row = conn.execute("SELECT payload FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
        return json.loads(row["payload"])

    @tool
    def get_claim() -> str:
        """Get the claim: the customer's story, the items claimed and the supporting documents."""
        return "<claim_data>\n" + json.dumps(claim()) + "\n</claim_data>"

    @tool
    def get_policy() -> str:
        """Get the policy for the trip: plan, add-ons, purchase date, trip dates and travelers."""
        return json.dumps(ref.policy(claim()["policy_id"]))

    @tool
    def get_customer_history() -> str:
        """Get the customer's name, customer-since date and earlier claims."""
        customer = ref.customer(claim()["customer_id"])
        return json.dumps({
            "name": customer["name"],
            "customer_since": customer["customer_since"],
            "previous_claims": customer["previous_claims"],
        })

    @tool
    def get_plan() -> str:
        """Get the limits, waiting periods and deductible of the policy's plan, and its add-ons."""
        policy = ref.policy(claim()["policy_id"])
        add_ons = [a for a in ref.add_ons if a["add_on"] in policy["add_ons"]]
        return json.dumps({"plan": ref.plan(policy["plan"]), "add_ons": add_ons})

    @tool
    def list_sections(document: str) -> str:
        """List the numbered sections of a document: 'policy_wording' (the contract) or 'claims_manual'."""
        try:
            return json.dumps([{"section": s.number, "title": s.title} for s in docs.sections(document)])
        except KeyError as exc:
            return f"Error: {exc.args[0]}. {unknown}"

    read: set[tuple[str, int]] = set()

    @tool
    def read_section(document: str, section: int) -> str:
        """Read one numbered section of 'policy_wording' or 'claims_manual'."""
        if (document, section) in read:
            return f"You already read {document} section {section} above. Use that text."
        if len(read) >= MAX_SECTIONS:
            return f"Error: you have read {MAX_SECTIONS} sections, the most for one claim. Decide with what you have."
        try:
            text = docs.section(document, section).text
        except KeyError as exc:
            return f"Error: {exc.args[0]}. {unknown}"
        read.add((document, section))
        return text

    @tool
    def search_documents(query: str) -> str:
        """Find the sections of the policy wording and the claims manual that mention a word or phrase."""
        return json.dumps(docs.search(query))

    @tool
    def lookup_knowledge(topic: str) -> str:
        """Look up public facts (events, products, places). Give a topic such as 'events' or a search word."""
        term = topic.lower()
        facts = [f for f in ref.knowledge if f["topic"].lower() == term or term in f["fact"].lower()]
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
        limit = ref.company["payout_approval_limit"]
        return json.dumps(compute_payout(items, third_party_paid, deductible, benefit_max, limit))

    @tool
    def request_payment(amount: float) -> str:
        """Request payment of an approved claim. Amounts above the approval limit are held for a person."""
        limit = ref.company["payout_approval_limit"]
        return json.dumps(asdict(create_payment(conn, claim_id, amount, limit)))

    return [
        get_claim, get_policy, get_customer_history, get_plan,
        list_sections, read_section, search_documents, lookup_knowledge, calculate_payout, request_payment,
    ]
