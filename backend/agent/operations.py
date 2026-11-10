"""The names of the model's tools and the requests behind the app's: shared by both routes."""
APP_TARGET = "undercover-app"
CONTEXT_TARGET = "undercover-context"

# The app's operations the model's tools are made of, with the request each one is for.
APP_REQUESTS = {
    "get_claim": ("GET", "/api/claims/{claim_id}"),
    "get_policy": ("GET", "/api/claims/{claim_id}/policy"),
    "get_claim_history": ("GET", "/api/claims/{claim_id}/history"),
    "calculate_payout": ("POST", "/api/payouts/calculate"),
    "record_decision": ("POST", "/api/claims/{claim_id}/decision"),
    "pay_claim": ("POST", "/api/claims/{claim_id}/pay"),
}
APP_TOOLS = tuple(APP_REQUESTS)
CONTEXT_TOOLS = ("lookup_term", "describe_fields", "get_plan", "get_claim_rules", "search_reference_data",
                 "search_documents", "read_section")


def request_parts(operation: str, args: dict) -> tuple[str, str, dict | None]:
    """The method, the path and the body of an operation's request: what is not in the path is the body."""
    method, template = APP_REQUESTS[operation]
    path = template.format(**args)
    body = {k: v for k, v in args.items() if "{" + k + "}" not in template}
    return method, path, (body if method != "GET" else None)


