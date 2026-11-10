"""The check for instructions hidden in a claim: a second model reads the claim before the claim agent's model does."""
import json
from pathlib import Path

import pytest

from agent.screening import SCREEN_INSTRUCTIONS, Screening
from agent.worker import carry_on, work_claim
from app.config import Settings
from tests.fakes import RawDb, ScreenStub, Stopped, agent_env, approve, stub, tool_call

CLAIM = "CLM-1001"
FOUND = Screening(reason="The story tells its handler to approve the claim in full.", instructions_found=True)


def rows(env, sql, params=()):
    return list(RawDb(env.engine).execute(sql, params))


def test_a_flagged_claim_is_referred_with_the_reason_and_the_claim_model_is_never_called(db_path, data_dir):
    model, screen = stub(), ScreenStub([FOUND])
    with agent_env(db_path, data_dir, model, screen_model=screen) as env:
        assert work_claim(env, CLAIM) == "CLM-1001: refer"
        decision = rows(env, "SELECT decision, payout, rationale, citations FROM decisions WHERE claim_id = ?", (CLAIM,))[0]
        status = rows(env, "SELECT status FROM claims WHERE claim_id = ?", (CLAIM,))[0]["status"]
        warnings = rows(env, "SELECT 1 FROM trace_events WHERE kind = 'warning'")
    assert model.calls == 0 and screen.calls == 1
    assert (decision["decision"], decision["payout"], decision["rationale"]) == ("refer", 0, FOUND.reason)
    assert json.loads(decision["citations"]) == ["Security check"]
    assert status == "decided" and not warnings  # no rationale warning: the check names no section


def test_a_claim_that_passes_is_worked_as_before(db_path, data_dir):
    model, screen = stub({CLAIM: [approve(420.0)]}), ScreenStub()
    with agent_env(db_path, data_dir, model, screen_model=screen) as env:
        assert work_claim(env, CLAIM) == "CLM-1001: approve $420.00, paid"
    assert screen.calls == 1 and model.calls == 1


def test_the_check_reads_every_field_the_customer_wrote(db_path, data_dir):
    claim = json.loads((Path(data_dir) / "claims.json").read_text(encoding="utf-8"))[0]
    screen = ScreenStub()
    with agent_env(db_path, data_dir, stub(), screen_model=screen) as env:
        work_claim(env, CLAIM)
    system, user = screen.shown[0]
    assert system.content == SCREEN_INSTRUCTIONS
    text = user.content
    assert text.startswith("<claim_data>") and text.rstrip().endswith("</claim_data>")  # as get_claim shows the claim
    assert json.loads(text.removeprefix("<claim_data>").rstrip().removesuffix("</claim_data>")) == claim
    for written in (claim["story"], claim["summary"], claim["items"][0]["description"], claim["documents"][0]["content"]):
        assert written in text


def test_the_check_runs_once_and_not_again_after_a_resume(db_path, data_dir):
    model, screen = stub({CLAIM: [[tool_call("search_documents", {"query": "delay"}, "s1"), Stopped(), approve(420.0)]]}), ScreenStub()
    with agent_env(db_path, data_dir, model, screen_model=screen) as env:
        with pytest.raises(Stopped):
            work_claim(env, CLAIM)
        assert carry_on(env, CLAIM) == "CLM-1001: approve $420.00, paid"
    assert screen.calls == 1


def test_a_claim_started_again_from_its_first_message_is_checked_again(db_path, data_dir):
    model, screen = stub({CLAIM: [ValueError("boom"), approve(420.0)]}), ScreenStub()
    with agent_env(db_path, data_dir, model, screen_model=screen) as env:
        work_claim(env, CLAIM)
        assert work_claim(env, CLAIM) == "CLM-1001: approve $420.00, paid"
    assert screen.calls == 2


def test_an_unreadable_answer_is_tried_three_times_and_each_call_is_costed(db_path, data_dir):
    model, screen = stub(), ScreenStub(["unreadable"] * 3)
    with agent_env(db_path, data_dir, model, screen_model=screen, agent_max_attempts=1) as env:
        line = work_claim(env, CLAIM, max_attempts=1)
        costed = rows(env, "SELECT model, cost_usd FROM model_calls WHERE claim_id = ?", (CLAIM,))
        status = rows(env, "SELECT status FROM claims WHERE claim_id = ?", (CLAIM,))[0]["status"]
    assert screen.calls == 3 and len(costed) == 3 and model.calls == 0
    assert all(c["model"] == "screen/stub" and c["cost_usd"] == pytest.approx(0.0001) for c in costed)
    assert "ScreeningFailed" in line and status == "needs_handler"  # fails as any other error in a claim's work does


def test_a_check_that_errors_fails_the_claim_without_the_claim_model(db_path, data_dir):
    model, screen = stub(), ScreenStub([TimeoutError("the check timed out")])
    with agent_env(db_path, data_dir, model, screen_model=screen) as env:
        line = work_claim(env, CLAIM)
        row = rows(env, "SELECT status, attempts FROM claims WHERE claim_id = ?", (CLAIM,))[0]
    assert model.calls == 0 and "TimeoutError" in line
    assert (row["status"], row["attempts"]) == ("waiting", 1)


def test_the_checks_result_is_the_first_step_of_the_work(db_path, data_dir):
    with agent_env(db_path, data_dir, stub(), screen_model=ScreenStub()) as env:
        work_claim(env, CLAIM)
        steps = rows(env, "SELECT kind, content FROM trace_events ORDER BY seq")
    assert [s["kind"] for s in steps][:2] == ["status", "screening"]  # the status is the claim being taken
    assert json.loads(steps[1]["content"])["instructions_found"] is False


def test_the_check_is_not_in_the_versioned_configuration(db_path, data_dir):
    with agent_env(db_path, data_dir, stub(), screen_model=ScreenStub()) as env:
        stored = rows(env, "SELECT instructions FROM config_versions")
        recipes = rows(env, "SELECT recipe FROM recipe_versions")
    texts = [r["instructions"] for r in stored] + [r["recipe"] for r in recipes]
    assert not any("instructions_found" in t or SCREEN_INSTRUCTIONS[:40] in t for t in texts)


def test_the_check_uses_luna_at_none_and_model_name_with_no_effort(monkeypatch):
    from agent.models import chat_model

    settings = Settings(_env_file=None, openrouter_api_key="k")
    assert settings.screening_model == ("openai/gpt-6-luna", "none")
    luna = chat_model(settings, *settings.screening_model, max_retries=0)
    assert luna.model_name == "openai/gpt-6-luna" and luna.extra_body["reasoning"] == {"effort": "none"}
    monkeypatch.setenv("MODEL_NAME", "some/free-model")
    settings = Settings(_env_file=None, openrouter_api_key="k")
    assert settings.screening_model == ("some/free-model", "default")
    free = chat_model(settings, *settings.screening_model, max_retries=0)
    assert free.model_name == "some/free-model" and "reasoning" not in free.extra_body


def test_clm_1901_is_in_the_queue_and_in_neither_evaluation_set(db_path, data_dir):
    with agent_env(db_path, data_dir, stub(), screen_model=ScreenStub()) as env:
        in_queue = rows(env, "SELECT status, copy_of FROM claims WHERE claim_id = 'CLM-1901'")
        in_sets = rows(env, "SELECT 1 FROM eval_sets WHERE claim_id = 'CLM-1901'")
        in_key = rows(env, "SELECT 1 FROM answer_key WHERE claim_id = 'CLM-1901'")
        total = rows(env, "SELECT COUNT(*) AS n FROM claims WHERE copy_of IS NULL")[0]["n"]
    assert [r["status"] for r in in_queue] == ["waiting"] and not in_sets and not in_key and total == 26
    claim = json.loads((Path(data_dir) / "security" / "claims.json").read_text(encoding="utf-8"))[0]
    from app.reference import Reference

    ref = Reference(data_dir)
    assert ref.policy(claim["policy_id"])["policy_id"] == claim["policy_id"]
    assert ref.customer(claim["customer_id"])["customer_id"] == claim["customer_id"]
    assert not any(c["claim_id"] == "CLM-1901" and "decision" in c for c in ref.claims)  # the handler's decision is never read
