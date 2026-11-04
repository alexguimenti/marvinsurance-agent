from app.config import Settings


def test_settings_reads_agent_model_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "x/y")
    assert Settings().agent_model == "x/y"


def test_temperature_defaults_to_zero(monkeypatch):
    monkeypatch.delenv("AGENT_TEMPERATURE", raising=False)
    assert Settings(_env_file=None).agent_temperature == 0.0


def write_toml(tmp_path, text):
    path = tmp_path / "agent.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_agent_toml_sets_model_temperature_and_reasoning(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONFIG_FILE", write_toml(tmp_path, (
        '[agent]\nmodel = "some/model"\ntemperature = 0.3\nreasoning_effort = "high"\n'
    )))
    for var in ("AGENT_MODEL", "AGENT_TEMPERATURE", "AGENT_REASONING_EFFORT"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert (s.agent_model, s.agent_temperature, s.agent_reasoning_effort) == ("some/model", 0.3, "high")


def test_environment_overrides_agent_toml(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONFIG_FILE", write_toml(tmp_path, '[agent]\nmodel = "from/toml"\n'))
    monkeypatch.setenv("AGENT_MODEL", "from/env")
    assert Settings(_env_file=None).agent_model == "from/env"


def test_reasoning_defaults_to_low_when_not_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONFIG_FILE", write_toml(tmp_path, '[agent]\nmodel = "m/x"\n'))
    monkeypatch.delenv("AGENT_REASONING_EFFORT", raising=False)
    assert Settings(_env_file=None).agent_reasoning_effort == "low"


def test_missing_agent_toml_falls_back_to_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONFIG_FILE", str(tmp_path / "nope.toml"))
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    assert Settings(_env_file=None).agent_model


def test_invalid_reasoning_effort_is_rejected(monkeypatch, tmp_path):
    import pytest
    from pydantic import ValidationError

    monkeypatch.setenv("AGENT_CONFIG_FILE", write_toml(tmp_path, '[agent]\nreasoning_effort = "extreme"\n'))
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_repo_agent_toml_is_valid(monkeypatch):
    import tomllib

    from app.config import CONFIG_FILE

    monkeypatch.delenv("AGENT_CONFIG_FILE", raising=False)
    for var in ("AGENT_MODEL", "AGENT_TEMPERATURE", "AGENT_REASONING_EFFORT"):
        monkeypatch.delenv(var, raising=False)
    table = tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8"))["agent"]
    assert table["model"]
    assert Settings(_env_file=None).agent_model == table["model"]


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
    for var in ("AGENT_MODEL", "AGENT_REASONING_EFFORT", "AGENT_MAX_ATTEMPTS"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.agent_model == "deepseek/deepseek-v4.1-flash"
    assert s.agent_reasoning_effort == "low"
    assert s.agent_max_attempts == 2
    assert s.agent_step_retries == 2
    assert s.agent_claim_timeout_s == 300
    assert s.agent_max_steps == 40
    assert s.agent_run_max_steps == 400


def test_repo_agent_toml_uses_default_model(monkeypatch):
    from app.config import _agent_toml

    monkeypatch.delenv("AGENT_CONFIG_FILE", raising=False)
    table = _agent_toml()
    assert table["agent_model"] == "deepseek/deepseek-v4.1-flash"
    assert table["agent_reasoning_effort"] == "low"


def test_env_overrides_toml_model(monkeypatch):
    monkeypatch.delenv("AGENT_CONFIG_FILE", raising=False)
    monkeypatch.setenv("AGENT_MODEL", "x/y")
    assert Settings(_env_file=None).agent_model == "x/y"


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
