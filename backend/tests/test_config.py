from app.config import Settings


def test_default_model_is_deepseek_with_low_effort(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONFIG_FILE", str(tmp_path / "none.toml"))
    settings = Settings(_env_file=None)
    assert settings.agent_model == "deepseek/deepseek-v4.1-flash" and settings.reasoning_effort == "low"


def test_model_name_chooses_another_model_with_no_effort(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "anthropic/claude-haiku-5-5")
    settings = Settings(_env_file=None)
    assert settings.agent_model == "anthropic/claude-haiku-5-5" and settings.reasoning_effort is None


def test_another_model_sends_no_reasoning(monkeypatch):
    from agent.models import chat_model

    monkeypatch.setenv("MODEL_NAME", "anthropic/claude-haiku-5-5")
    assert "reasoning" not in chat_model(Settings(_env_file=None, openrouter_api_key="test")).extra_body


def test_the_default_model_sends_low_reasoning(monkeypatch, tmp_path):
    from agent.models import chat_model

    monkeypatch.setenv("AGENT_CONFIG_FILE", str(tmp_path / "none.toml"))
    assert chat_model(Settings(_env_file=None, openrouter_api_key="test")).extra_body["reasoning"] == {"effort": "low"}


def test_model_name_in_dot_env_alone_is_ignored(tmp_path):
    (tmp_path / ".env").write_text("MODEL_NAME=x/y\n")
    assert Settings(_env_file=tmp_path / ".env").agent_model == "deepseek/deepseek-v4.1-flash"


def test_temperature_defaults_to_zero(monkeypatch):
    monkeypatch.delenv("AGENT_TEMPERATURE", raising=False)
    assert Settings(_env_file=None).agent_temperature == 0.0


def write_toml(tmp_path, text):
    path = tmp_path / "agent.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_agent_toml_sets_model_and_temperature(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONFIG_FILE", write_toml(tmp_path, '[agent]\nmodel = "some/model"\ntemperature = 0.3\n'))
    monkeypatch.delenv("AGENT_TEMPERATURE", raising=False)
    s = Settings(_env_file=None)
    assert (s.agent_model, s.agent_temperature, s.reasoning_effort) == ("some/model", 0.3, None)


def test_model_name_overrides_agent_toml(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONFIG_FILE", write_toml(tmp_path, '[agent]\nmodel = "from/toml"\n'))
    monkeypatch.setenv("MODEL_NAME", "from/env")
    assert Settings(_env_file=None).agent_model == "from/env"


def test_repo_agent_toml_is_valid(monkeypatch):
    import tomllib

    from app.config import CONFIG_FILE

    monkeypatch.delenv("AGENT_CONFIG_FILE", raising=False)
    monkeypatch.delenv("AGENT_TEMPERATURE", raising=False)
    table = tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8"))["agent"]
    assert "reasoning_effort" not in table
    assert Settings(_env_file=None).agent_model == table["model"] == "deepseek/deepseek-v4.1-flash"


def test_max_attempts_defaults_to_two_and_is_read_from_agent_toml(monkeypatch, tmp_path):
    for var in ("AGENT_MAX_ATTEMPTS",):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AGENT_CONFIG_FILE", str(tmp_path / "none.toml"))
    assert Settings(_env_file=None).agent_max_attempts == 2
    monkeypatch.setenv("AGENT_CONFIG_FILE", write_toml(tmp_path, "[agent]\nmax_attempts = 5\n"))
    assert Settings(_env_file=None).agent_max_attempts == 5


def test_max_attempts_must_be_at_least_one(monkeypatch, tmp_path):
    import pytest
    from pydantic import ValidationError

    monkeypatch.setenv("AGENT_CONFIG_FILE", write_toml(tmp_path, "[agent]\nmax_attempts = 0\n"))
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_defaults_without_toml(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONFIG_FILE", str(tmp_path / "none.toml"))
    monkeypatch.delenv("AGENT_MAX_ATTEMPTS", raising=False)
    s = Settings(_env_file=None)
    assert s.agent_model == "deepseek/deepseek-v4.1-flash"
    assert s.reasoning_effort == "low"
    assert s.agent_max_attempts == 2
    assert s.agent_step_retries == 2
    assert s.agent_claim_timeout_s == 300
    assert s.agent_max_model_calls == 30


def test_database_url_in_dot_env_alone_is_ignored(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://nope/nope\nUNDERCOVER_AGENT=http://nope\n")
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=tmp_path / ".env")
    assert settings.database_url is None
    assert settings.agent_url == "http://127.0.0.1:8002"


def test_database_url_and_agent_address_in_the_environment_are_used(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/d")
    monkeypatch.setenv("UNDERCOVER_AGENT", "http://127.0.0.1:8003")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql://u:p@h/d"
    assert settings.agent_url == "http://127.0.0.1:8003"


def test_a_model_never_asks_for_more_tokens_than_a_claim_needs():
    # OpenRouter holds back credit for the most a call may produce: a model's own ceiling (131,072) needs a rich key
    from agent.models import chat_model

    assert chat_model(Settings(_env_file=None, openrouter_api_key="test")).max_tokens == 16384
    assert chat_model(Settings(_env_file=None, openrouter_api_key="test", agent_max_tokens=4096)).max_tokens == 4096
