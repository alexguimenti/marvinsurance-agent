from langchain_openai import ChatOpenAI

from app.config import Settings

OPENROUTER_URL = "https://openrouter.ai/api/v1"


def chat_model(settings: Settings, model: str | None = None, effort: str | None = None, **more) -> ChatOpenAI:
    """An OpenRouter model: the one asked for (a configuration version's), or the configured one. Effort `default`
    sends none."""
    level = effort if effort is not None else settings.reasoning_effort
    retries = more.pop("max_retries", settings.agent_step_retries)
    return ChatOpenAI(
        model=model or settings.agent_model,
        **more,
        api_key=settings.openrouter_api_key,
        base_url=OPENROUTER_URL,
        temperature=settings.agent_temperature,
        max_tokens=settings.agent_max_tokens,  # without it OpenRouter reserves the model's own ceiling (131,072) from the key's credit
        timeout=settings.agent_claim_timeout_s,
        max_retries=retries,
        extra_body={
            "usage": {"include": True},  # OpenRouter then reports the dollar cost of each call
            **({"reasoning": {"effort": level}} if level and level != "default" else {}),
        },
    )
