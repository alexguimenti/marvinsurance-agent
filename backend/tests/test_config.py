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


def test_reasoning_is_off_when_not_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONFIG_FILE", write_toml(tmp_path, '[agent]\nmodel = "m/x"\n'))
    monkeypatch.delenv("AGENT_REASONING_EFFORT", raising=False)
    assert Settings(_env_file=None).agent_reasoning_effort is None


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
