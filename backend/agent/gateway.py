"""The Gateway route: every tool is an MCP tool named `<target>___<operation>`, and every request is signed with AWS
Signature Version 4 for `bedrock-agentcore`, with the credentials and region the AWS CLI uses."""
import json
from typing import Any, Callable

import httpx
from botocore.auth import SigV4Auth as BotocoreSigV4Auth
from botocore.awsrequest import AWSRequest
from mcp.shared.exceptions import McpError

from agent.mcp_bridge import ToolRefused, call_tool
from agent.operations import APP_TARGET, APP_TOOLS, CONTEXT_TARGET, CONTEXT_TOOLS


class GatewayRefused(Exception):
    """The Gateway's Policy refused the call. The app and the context layer never saw it."""


class SigV4Auth(httpx.Auth):
    """Sign each request with AWS Signature Version 4, as boto3 does for its own calls."""

    requires_request_body = True

    def __init__(self, session, service: str = "bedrock-agentcore"):
        self.credentials = session.get_credentials()
        self.region = session.region_name
        self.service = service

    def auth_flow(self, request):
        headers = {"Host": request.url.netloc.decode(), "Content-Type": request.headers.get("content-type", "")}
        aws_request = AWSRequest(method=request.method, url=str(request.url), data=request.content, headers=headers)
        BotocoreSigV4Auth(self.credentials.get_frozen_credentials(), self.service, self.region).add_auth(aws_request)
        for name in ("Authorization", "X-Amz-Date", "X-Amz-Security-Token"):
            if name in aws_request.headers:
                request.headers[name] = aws_request.headers[name]
        yield request


def _reason(text: str) -> str:
    """What the app said, from the JSON its error comes wrapped in."""
    try:
        detail = json.loads(text).get("detail", text)
    except (ValueError, AttributeError):
        return text
    return detail if isinstance(detail, str) else json.dumps(detail)


class GatewayRoute:
    kind = "gateway"

    def __init__(self, url: str, auth: httpx.Auth | None, call: Callable[..., str] = call_tool):
        self.url, self.auth, self._call = url, auth, call

    def _tool(self, target: str, tool: str, args: dict) -> Any:
        try:
            return json.loads(self._call(self.url, f"{target}___{tool}", args, self.auth))
        except McpError as exc:  # raised by the MCP client, not returned as a result: the Policy's refusal
            raise GatewayRefused(str(exc)) from exc
        except ToolRefused as exc:  # the app's or the context layer's own refusal
            raise ToolRefused(_reason(str(exc))) from exc

    def app(self, operation: str, args: dict) -> Any:
        return self._tool(APP_TARGET, operation, args)

    def context(self, tool: str, args: dict) -> Any:
        return self._tool(CONTEXT_TARGET, tool, args)

    def shown_name(self, tool: str) -> str:
        if tool in CONTEXT_TOOLS:
            return f"{CONTEXT_TARGET}___{tool}"
        return f"{APP_TARGET}___{tool}" if tool in APP_TOOLS else tool  # the agent's own tools keep their names
