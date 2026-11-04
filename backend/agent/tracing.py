"""Langfuse tracing for claims. On only when the keys are in the process environment, and never fails a claim."""
import logging
import os
from functools import lru_cache
from typing import Callable

log = logging.getLogger(__name__)


def _enabled() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


@lru_cache
def _client():
    from langfuse import Langfuse  # imported here so tests and runs without keys never load a client

    return Langfuse(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        base_url=os.environ.get("LANGFUSE_BASE_URL"),
    )


def _handler():
    from langfuse.langchain import CallbackHandler

    _client()
    return CallbackHandler(public_key=os.environ["LANGFUSE_PUBLIC_KEY"])


def warm_up_tracing() -> None:
    """Load the client now, not during the first sign-off."""
    if _enabled():
        try:
            _handler()
        except Exception as exc:
            log.warning("Langfuse is not available: %s", exc)


def session_name(claim_id: str, thread_id: str) -> str:
    return f"{claim_id}:{thread_id}"


def trace_config(claim_id: str, thread_id: str, model: str, handler_factory: Callable = _handler) -> dict:
    """What a graph run needs for its traces to land in the claim's session."""
    config: dict = {
        "metadata": {
            "langfuse_session_id": session_name(claim_id, thread_id),
            "langfuse_trace_name": f"claim {claim_id}",
            "langfuse_tags": [claim_id, model],
        }
    }
    if _enabled():
        try:
            config["callbacks"] = [handler_factory()]
        except Exception as exc:  # tracing must never stop the work
            log.warning("Langfuse is not available: %s", exc)
    return config


def flush_traces() -> None:
    """Traces are sent in the background; this waits until they are out."""
    if not _enabled():
        return
    try:
        _client().flush()
    except Exception as exc:
        log.warning("could not flush the traces: %s", exc)
