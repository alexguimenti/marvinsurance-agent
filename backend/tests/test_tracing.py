from agent.tracing import flush_traces, session_name, trace_config

KEYS = {"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk", "LANGFUSE_BASE_URL": "http://langfuse.invalid"}


def set_keys(monkeypatch):
    for name, value in KEYS.items():
        monkeypatch.setenv(name, value)


def test_session_named_after_claim_and_thread():
    assert session_name("CLM-1003", "CLM-1003-1") == "CLM-1003:CLM-1003-1"


def test_no_keys_no_callbacks():
    config = trace_config("CLM-1003", "CLM-1003-1", "some/model")
    assert "callbacks" not in config
    assert config["metadata"]["langfuse_session_id"] == "CLM-1003:CLM-1003-1"


def test_keys_add_the_handler(monkeypatch):
    set_keys(monkeypatch)
    handler = object()
    config = trace_config("CLM-1003", "CLM-1003-1", "some/model", handler_factory=lambda: handler)
    assert config["callbacks"] == [handler]
    assert config["metadata"]["langfuse_trace_name"] == "claim CLM-1003"
    assert config["metadata"]["langfuse_tags"] == ["CLM-1003", "some/model"]


def test_keys_in_dot_env_alone_do_nothing(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\n")
    monkeypatch.chdir(tmp_path)
    assert "callbacks" not in trace_config("CLM-1003", "CLM-1003-1", "some/model")


def test_a_failing_handler_factory_never_fails_the_claim(monkeypatch):
    set_keys(monkeypatch)

    def broken():
        raise RuntimeError("langfuse is down")

    assert "callbacks" not in trace_config("CLM-1003", "CLM-1003-1", "some/model", handler_factory=broken)


def test_flush_without_keys_does_nothing():
    flush_traces()
