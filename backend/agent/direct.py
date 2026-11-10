"""The direct route: the app with the agent's key, and the context layer over MCP with no signature."""
import json
from typing import Any

from agent import connection
from agent.mcp_bridge import call_tool
from agent.operations import CONTEXT_TOOLS, request_parts

DEFAULT_CONTEXT = "http://127.0.0.1:8006/mcp"


class DirectRoute:
    kind = "direct"

    def __init__(self, context_url: str = DEFAULT_CONTEXT, call=call_tool):
        self.context_url, self._call = context_url, call

    def app(self, operation: str, args: dict) -> Any:
        """The app's answer. A refusal is `Refused`, and an unknown claim is a LookupError, as everywhere in the agent."""
        method, path, body = request_parts(operation, args)
        return connection.call(method, path, json=body)

    def context(self, tool: str, args: dict) -> Any:
        return json.loads(self._call(self.context_url, tool, args))

    def shown_name(self, tool: str) -> str:
        return tool
