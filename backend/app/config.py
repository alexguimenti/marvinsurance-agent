import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, InitSettingsSource, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "agent.toml"
DEFAULT_AGENT_URL = "http://127.0.0.1:8080"  # the port AgentCore apps use
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


class Settings(BaseSettings):
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
    data_dir: str = str(ROOT / "data")
    context_dir: str = str(ROOT / "backend" / "context")  # data governance's files: the app reads them only to seed
    database_url: str | None = None  # from the process environment only: see model_post_init
    agent_url: str = DEFAULT_AGENT_URL
    agent_runtime_arn: str | None = None  # UNDERCOVER_AGENT_RUNTIME, from the process environment only: see model_post_init
    supabase_url: str | None = None  # from the process environment only: see model_post_init
    model_name: str | None = None  # MODEL_NAME, when the platform runs on one model: see model_post_init
    on_lambda: bool = False  # from AWS_LAMBDA_FUNCTION_NAME: see model_post_init
    dashboard_dir: str = str(ROOT / "dashboard" / "dist")  # the dashboard's build: served at / when it exists
    api_key: str | None = None  # from the process environment only: see model_post_init
    judge_url: str = "https://openrouter.ai"  # the origin of the judge's endpoint; the tests point it at a stand-in

    def model_post_init(self, _context) -> None:
        # A .env file must not be enough to point a process at Supabase or at a running agent:
        # `npm run dev` passes these in the environment, and the tests never do.
        self.database_url = os.environ.get("DATABASE_URL") or None
        self.agent_url = os.environ.get("UNDERCOVER_AGENT") or DEFAULT_AGENT_URL
        self.agent_runtime_arn = os.environ.get("UNDERCOVER_AGENT_RUNTIME") or None
        self.on_lambda = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
        self.model_name = os.environ.get("MODEL_NAME") or None  # the one place MODEL_NAME is read
        self.agent_model = self.model_name or self.agent_model
        self.judge_url = os.environ.get("OPENROUTER_DECISIONS_URL") or self.judge_url
        self.api_key = os.environ.get("UNDERCOVER_API_KEY") or None  # the agent's key to the backend
        self.supabase_url = os.environ.get("VITE_SUPABASE_URL") or None  # the same address the dashboard signs in at

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
