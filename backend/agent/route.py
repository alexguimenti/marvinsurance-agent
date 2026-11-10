"""The two ways to the model's tools. The app's operations and the context layer's tools go either direct (the app with the
agent's key, the context layer over unsigned MCP) or through the Gateway (MCP tools, signed with AWS Signature Version 4).
Which one is read from the environment each time a claim graph is built, never when this module is imported.

The agent's own bookkeeping (starting a claim, its steps, model calls, work times, failures) is not here: it goes straight
to the app on both routes, through `agent.api`."""
import os
from typing import Any, Literal, Protocol

import boto3

from agent.direct import DEFAULT_CONTEXT, DirectRoute
from agent.gateway import GatewayRoute, SigV4Auth
from agent.operations import APP_REQUESTS, APP_TARGET, APP_TOOLS, CONTEXT_TARGET, CONTEXT_TOOLS, request_parts  # noqa: F401


class Route(Protocol):
    kind: Literal["direct", "gateway"]

    def app(self, operation: str, args: dict) -> Any: ...

    def context(self, tool: str, args: dict) -> Any: ...

    def shown_name(self, tool: str) -> str: ...


def make_route() -> Route:
    url = os.environ.get("UNDERCOVER_GATEWAY_URL")
    if url:
        return GatewayRoute(url, SigV4Auth(boto3.Session()))
    return DirectRoute(os.environ.get("UNDERCOVER_CONTEXT") or DEFAULT_CONTEXT)
