"""The context layer as an MCP server: stateless, JSON responses, every tool read-only. On the laptop `python -m context`
serves it with uvicorn; on Lambda `handler` serves each Function URL event."""
from mangum import Mangum
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from context import tools

READ_ONLY = ToolAnnotations(readOnlyHint=True)


def build_server(lambda_host: bool = False) -> FastMCP:
    """A new server. On Lambda the Function URL's host is not 127.0.0.1, so the DNS rebinding check is off: the
    Function URL's IAM auth guards it there."""
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False) if lambda_host else None
    server = FastMCP("undercover-context", stateless_http=True, json_response=True, transport_security=security)

    @server.tool(annotations=READ_ONLY)
    def lookup_term(term: str) -> dict:
        """Look up a term the policy wording defines, with the sections it comes from."""
        return tools.lookup_term(term)

    @server.tool(annotations=READ_ONLY)
    def describe_fields(record: str) -> dict:
        """Describe the fields of a claim, a policy or a previous_claim, as the data dictionary defines them."""
        return tools.describe_fields(record)

    @server.tool(annotations=READ_ONLY)
    def get_plan(plan: str) -> dict:
        """Get one plan's limits, waiting periods, deductible and add-ons: economy, premium or first."""
        return tools.get_plan(plan)

    @server.tool(annotations=READ_ONLY)
    def get_claim_rules() -> dict:
        """Get the rules claims operations applies to every claim."""
        return tools.get_claim_rules()

    @server.tool(annotations=READ_ONLY)
    def search_reference_data(query: str) -> dict:
        """Find the outside facts (events, places, products) that contain every word of the query."""
        return tools.search_reference_data(query)

    @server.tool(annotations=READ_ONLY)
    def search_documents(query: str) -> dict:
        """Find the sections of the policy wording and the claims manual that mention a word or phrase."""
        return tools.search_documents(query)

    @server.tool(annotations=READ_ONLY)
    def read_section(document: str, number: int) -> dict:
        """Read one numbered section of policy_wording or claims_manual."""
        return tools.read_section(document, number)

    return server


def handler(event, context):
    """The Lambda handler. FastMCP's session manager can run only once, and Mangum runs the app's startup and shutdown
    around every request, so each request gets a server of its own. The files are read once, at import."""
    return Mangum(build_server(lambda_host=True).streamable_http_app(), lifespan="auto")(event, context)
