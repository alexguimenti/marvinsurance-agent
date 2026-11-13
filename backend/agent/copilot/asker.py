"""Who the Copilot is working for, as the graph's context. It reaches the graph and its tools through `context_schema` and
`ToolRuntime`, never through the config: LangGraph saves the config's values with each checkpoint, and the sign-in must never be
saved. The token is hidden from the repr, so a log line that shows the asker never shows it."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Asker:
    token: str = field(repr=False)  # the handler's Supabase access token, passed on with each request to the app
    handler: dict  # what the app said at the start of the turn: handler_id, name, role, can_approve_over_limit
    conversation_id: str
    turn_id: int
