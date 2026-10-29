import json
import sqlite3
from dataclasses import asdict

from langchain_core.tools import BaseTool, tool

from app.payments import request_payment as create_payment
from app.payout import LineItem, calculate_payout as compute_payout
from app.reference import Reference


def build_tools(conn: sqlite3.Connection, ref: Reference, claim_id: str) -> list[BaseTool]:
    """Read-only tools bound to a single claim. None of them takes a path or a claim id."""

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
    def get_policy_wording() -> str:
        """Get the full policy wording, the contract with the customer."""
        return ref.wording

    @tool
    def lookup_knowledge(topic: str) -> str:
        """Look up public facts (events, products, places). Give a topic such as 'events' or a search word."""
        term = topic.lower()
        facts = [f for f in ref.knowledge if f["topic"].lower() == term or term in f["fact"].lower()]
        return json.dumps(facts)

    @tool
    def calculate_payout(
        items: list[LineItem],
        third_party_paid: float = 0.0,
        deductible: float = 0.0,
        benefit_max: float | None = None,
    ) -> str:
        """Work out the payout (wording section 10). Pass each item with the per-item limit that applies to it."""
        return json.dumps(compute_payout(items, third_party_paid, deductible, benefit_max))

    @tool
    def request_payment(amount: float) -> str:
        """Request payment of an approved claim. Amounts above the approval limit are held for a person."""
        limit = ref.company["payout_approval_limit"]
        return json.dumps(asdict(create_payment(conn, claim_id, amount, limit)))

    return [
        get_claim, get_policy, get_customer_history, get_plan,
        get_policy_wording, lookup_knowledge, calculate_payout, request_payment,
    ]
