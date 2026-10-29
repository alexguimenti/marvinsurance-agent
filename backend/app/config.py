import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, InitSettingsSource, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "agent.toml"


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
    agent_model: str = "openai/gpt-4o-mini"
    agent_temperature: float = 0.0
    agent_reasoning_effort: Literal["low", "medium", "high"] | None = None
    agent_provider: Literal["openrouter", "scripted"] = "openrouter"
    db_path: str = str(ROOT / "farting.db")
    data_dir: str = str(ROOT / "data")

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        # priority: constructor, environment, .env, then agent.toml
        return (init_settings, env_settings, dotenv_settings, InitSettingsSource(settings_cls, init_kwargs=_agent_toml()))


@lru_cache
def get_settings() -> Settings:
    return Settings()
