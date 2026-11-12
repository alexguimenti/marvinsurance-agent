"""The agent's own settings, so the agent imports nothing from the app: on Runtime the package is `runtime/` with `agent/` beside
it. The names, the defaults and the places they are read from (the environment, `.env`, then `agent.toml`) are the app's, so
one `.env` serves both. `database_url` and `api_key` come from the process environment only: a `.env` file must not be enough
to point a process at Supabase."""
import os
import secrets
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, InitSettingsSource, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "agent.toml"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
DEFAULT_EFFORT = "low"
SCREENING_MODEL = ("openai/gpt-6-luna", "none")  # the security check: quick and cheap enough to read every claim


def _agent_toml() -> dict:
    """The [agent] table of agent.toml (or of AGENT_CONFIG_FILE), as agent_* settings."""
    path = Path(os.environ.get("AGENT_CONFIG_FILE", CONFIG_FILE))
    if not path.exists():
        return {}
    table = tomllib.loads(path.read_text(encoding="utf-8")).get("agent", {})
    return {f"agent_{key}": value for key, value in table.items()}


def key_matches(presented: str | None, key: str | None) -> bool:
    """Whether the key a request presented is the agent's, compared in constant time. No key set matches nothing."""
    return bool(key) and bool(presented) and secrets.compare_digest(presented.encode(), key.encode())


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    openrouter_api_key: str | None = None
    agent_model: str = DEFAULT_MODEL  # MODEL_NAME in the environment wins: see model_post_init
    agent_temperature: float = 0.0
    agent_max_attempts: int = Field(default=2, ge=1, le=10)  # attempts per claim before it is left for a handler
    agent_step_retries: int = Field(default=2, ge=0, le=5)  # retries of one model call (timeout, 429, 5xx)
    agent_claim_timeout_s: float = Field(default=300, gt=0)  # wall-clock limit for one claim attempt
    agent_max_model_calls: int = Field(default=30, ge=1)  # model calls for one claim
    agent_max_tokens: int = Field(default=16384, ge=256)  # most one call may produce: OpenRouter holds credit back for it
    agent_provider: Literal["openrouter", "scripted"] = "openrouter"
    db_path: str = str(ROOT / "marvin.db")
    database_url: str | None = None  # from the process environment only: see model_post_init
    model_name: str | None = None  # MODEL_NAME, when the platform runs on one model: see model_post_init
    api_key: str | None = None  # from the process environment only: see model_post_init

    def model_post_init(self, _context) -> None:
        self.database_url = os.environ.get("DATABASE_URL") or None
        self.model_name = os.environ.get("MODEL_NAME") or None
        self.agent_model = self.model_name or self.agent_model
        self.api_key = os.environ.get("UNDERCOVER_API_KEY") or None

    @property
    def reasoning_effort(self) -> str | None:
        """Low for the default model. Any other model runs with no effort set, since not every model takes one."""
        return DEFAULT_EFFORT if self.agent_model == DEFAULT_MODEL else None

    @property
    def screening_model(self) -> tuple[str, str]:
        """The model that checks each claim and its reasoning effort: Luna with none, or the one model the platform runs
        on (MODEL_NAME) with no effort set."""
        return (self.model_name, "default") if self.model_name else SCREENING_MODEL

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        # priority: constructor, environment, .env, then agent.toml
        return (init_settings, env_settings, dotenv_settings, InitSettingsSource(settings_cls, init_kwargs=_agent_toml()))
