from langchain_openai import ChatOpenAI

from app.config import Settings

OPENROUTER_URL = "https://openrouter.ai/api/v1"


def chat_model(settings: Settings) -> ChatOpenAI:
    """The configured OpenRouter model, for the claim agent."""
    return ChatOpenAI(
        model=settings.agent_model,
        api_key=settings.openrouter_api_key,
        base_url=OPENROUTER_URL,
        temperature=settings.agent_temperature,
        timeout=settings.agent_claim_timeout_s,
        max_retries=settings.agent_step_retries,
        extra_body={
            "usage": {"include": True},  # OpenRouter then reports the dollar cost of each call
            **({"reasoning": {"effort": settings.reasoning_effort}} if settings.reasoning_effort else {}),
        },
    )
