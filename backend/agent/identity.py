"""AgentCore Identity: how an AgentCore app on Runtime gets its credentials. Used by the claim agent's app and the Copilot's."""
import os
import re

# The CLI gives each credential's provider name in CREDENTIAL_<NAME>_NAME (AgentCore CLI 1.0.2) or AGENTCORE_CREDENTIAL_<NAME>_NAME (rc.5)
_CREDENTIAL = re.compile(r"^(?:AGENTCORE_)?CREDENTIAL_(.+)_NAME$")


def on_runtime() -> bool:
    """On Runtime the deployment gives the agent a setting for each credential it fetches from AgentCore Identity."""
    return any(_CREDENTIAL.match(name) for name in os.environ)


def fetch_credentials(only: set[str] | None = None) -> None:
    """Put each credential from AgentCore Identity into the setting of its name, where `.env` puts it on a laptop. With `only`, just those
    credentials: the Copilot never fetches the agent's API key. It reads the token Runtime sends with each request, so it runs while a
    request is handled, never on a thread of its own."""
    from bedrock_agentcore.identity.auth import requires_api_key

    for name in list(os.environ):
        found = _CREDENTIAL.match(name)
        if not found or (only is not None and found[1] not in only):
            continue

        @requires_api_key(provider_name=os.environ[name])  # makes its client when applied: so apply it here
        def fetch(*, api_key: str) -> str:
            return api_key

        os.environ[found[1]] = fetch()
