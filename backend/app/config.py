from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    openrouter_api_key: str | None = None
    agent_model: str = "openai/gpt-4o-mini"
    agent_temperature: float = 0.0
    agent_provider: Literal["openrouter", "scripted"] = "openrouter"
    db_path: str = str(ROOT / "farting.db")
    data_dir: str = str(ROOT / "data")


@lru_cache
def get_settings() -> Settings:
    return Settings()
