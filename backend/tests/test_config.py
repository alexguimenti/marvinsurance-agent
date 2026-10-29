from app.config import Settings


def test_settings_reads_agent_model_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "x/y")
    assert Settings().agent_model == "x/y"


def test_temperature_defaults_to_zero(monkeypatch):
    monkeypatch.delenv("AGENT_TEMPERATURE", raising=False)
    assert Settings(_env_file=None).agent_temperature == 0.0
