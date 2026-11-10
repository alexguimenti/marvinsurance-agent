"""The claim agent's memory of each customer, kept in AgentCore Memory (in our own AWS account).

When a claim ends with a decision, two events go to memory with the customer as the actor and the claim as the session: the
claim, as the customer's message, and the decision, as the agent's. AgentCore turns the events into memories in the
background. The model can search what we remember about this claim's customer, and nobody else's (claims manual section 11).
The story and the documents never go to memory: a story is where a customer's text could try to instruct us, and the
documents hold whole medical records."""
import json
import logging
import os

import boto3
from langchain_core.messages import AIMessage, HumanMessage
from langgraph_checkpoint_aws import AgentCoreMemoryStore

log = logging.getLogger(__name__)
SEARCH_LIMIT = 5


def open_store():
    """The AgentCore Memory in UNDERCOVER_MEMORY_ID, in the AWS CLI's region, or None when memory is off. Read each time
    it is called, never at import."""
    memory_id = os.environ.get("UNDERCOVER_MEMORY_ID")
    if not memory_id:
        return None
    return AgentCoreMemoryStore(memory_id=memory_id, region_name=boto3.Session().region_name)


def save_claim(store, claim: dict, decision: dict) -> list[str]:
    """Save the claim and its decision as two events. Returns what was saved, in words, for the claim's steps."""
    customer, claim_id = claim["customer_id"], claim["claim_id"]
    said = {
        "summary": claim["summary"], "claim_type": claim["claim_type"], "incident_date": claim["incident_date"],
        "incident_location": claim["incident_location"],
        "items": [{"description": i["description"], "category": i["category"], "amount": i["amount"]} for i in claim["items"]],
    }
    decided = {k: decision[k] for k in ("decision", "payout", "citations", "rationale")}
    store.put((customer, claim_id), "claim", {"message": HumanMessage(json.dumps(said))})
    store.put((customer, claim_id), "decision", {"message": AIMessage(json.dumps(decided))})
    return [f"The claim: {claim['summary']}",
            f"The decision: {decision['decision']} ${decision['payout']:,.2f}, {', '.join(decision['citations'])}"]


def search(store, customer_id: str, claim_id: str, query: str) -> list[str]:
    """What is remembered about this customer, from everything under their name, leaving out the summary of the claim being
    worked: a claim worked again would otherwise find its own summary."""
    items = store.search(("customers", customer_id), query=query, limit=SEARCH_LIMIT)
    return [item.value["content"] for item in items if claim_id not in tuple(item.namespace)]
