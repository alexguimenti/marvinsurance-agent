"""The judge grades each claim of an evaluation against the handler's reason, in the backend, with Jev."""
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.judge as judge_module
from agent.dispatcher import dispatch
from agent.scripted import ScriptedChatModel
from agent.service import create_service
from app.api import create_app
from app.config import Settings
from app.evaluations import start_evaluation
from app.judge import GRADES, JEV, JUDGE_QUESTIONS, grade_evaluation, judge
from app.scoring import measures
from app.usage import run_totals
from tests.fakes import MINT, RawDb, agent_env

HANDLER = {"decision": "approve", "reason": "A covered delay of 19 hours.", "reference": "Policy wording, section 3"}
AGENT = {"decision": "approve", "rationale": "The delay passed the waiting period under section 3.", "section": "Policy wording, section 3"}
SETTINGS = Settings(_env_file=None, openrouter_api_key="test-key")


def answers(probabilities=None, score=2.0, grounds=0.9, section=0.8, cost=0.0001):
    """What Jev's Decisions endpoint returns, as the SDK's objects look."""
    probabilities = probabilities or {"0": 0.05, "1": 0.15, "2": 0.7, "3": 0.1}
    return SimpleNamespace(
        answers={
            "grade": SimpleNamespace(score=score, probabilities=probabilities),
            "same_grounds": SimpleNamespace(noul=grounds),
            "same_section": SimpleNamespace(noul=section),
        },
        usage=SimpleNamespace(cost=cost),
    )


class StubJudge:
    """Stands in for the openrouter client: records every call and answers from a function or a fixed reply."""

    def __init__(self, reply=None, delay=0.0, fail=False):
        self.calls, self.reply, self.delay, self.fail = [], reply or answers(), delay, fail
        self.alpha = SimpleNamespace(decisions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise TimeoutError("the judge took too long")
        return self.reply


def test_the_letter_is_the_most_likely_level_not_the_score_rounded():
    reply = answers(probabilities={"0": 0.05, "1": 0.45, "2": 0.10, "3": 0.40}, score=1.85)
    result = judge(HANDLER, AGENT, SETTINGS, StubJudge(reply))
    assert result["letter"] == "C"  # the score 1.85 would round to B
    assert result["score"] == 1.85


def test_the_grades_are_numbered_by_their_position_f_is_0_and_a_is_3():
    assert GRADES == ["F", "C", "B", "A"]
    assert [q["type"] for q in JUDGE_QUESTIONS.values()] == ["score", "noul", "noul"]


def test_the_two_checks_stay_as_probabilities_and_the_cost_is_kept():
    result = judge(HANDLER, AGENT, SETTINGS, StubJudge(answers(grounds=0.93, section=0.12, cost=0.00021)))
    assert (result["same_grounds"], result["same_section"], result["cost_usd"]) == (0.93, 0.12, 0.00021)


def test_the_call_is_jev_with_the_state_the_address_the_timeout_and_no_retries():
    stub = StubJudge()
    judge(HANDLER, AGENT, Settings(_env_file=None, openrouter_api_key="k"), stub)
    (call,) = stub.calls
    assert call["model"] == JEV == "typesafe/jev-1.13"
    assert call["state"] == {"handler": HANDLER, "agent": AGENT}
    assert call["questions"] is JUDGE_QUESTIONS
    assert call["server_url"] == "https://openrouter.ai"
    assert call["timeout_ms"] == 10_000
    assert call["retries"] is None  # off: finishing a run stays under 20 seconds


def test_the_address_comes_from_the_environment_once(monkeypatch):
    monkeypatch.setenv("OPENROUTER_DECISIONS_URL", "http://127.0.0.1:8005")
    assert Settings(_env_file=None).judge_url == "http://127.0.0.1:8005"


@pytest.fixture
def evaluated(db_path, data_dir):
    """An evaluation of five tiny claims, worked on the scripted model and not yet graded."""
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir, openrouter_api_key="k")
    with agent_env(db_path, data_dir, ScriptedChatModel()) as env:  # no key: finishing leaves the claims not graded
        run_id = start_evaluation(env.engine, env.ref, "evaluation", True, 5, "scripted", None)
        dispatch(env, run_id, 5)
        yield env, run_id, settings


def grades(env, run_id):
    return {r["claim_id"]: r for r in RawDb(env.engine).execute(
        "SELECT claim_id, score, letter, same_grounds, same_section, cost_usd, error FROM judge_grades WHERE run_id = ?", (run_id,))}


def test_every_claim_of_an_evaluation_is_graded_against_the_handlers_reason(evaluated):
    env, run_id, settings = evaluated
    stub = StubJudge()
    grade_evaluation(env.engine, run_id, settings, stub)
    rows = grades(env, run_id)
    assert len(rows) == 5 and len(stub.calls) == len([c for c in stub.calls])
    graded = [r for r in rows.values() if r["score"] is not None]
    assert all(r["letter"] == "B" for r in graded)
    # the handler's reason and reference went to the judge, from the answer key
    sent = [c["state"]["handler"] for c in stub.calls]
    assert all(s["reason"] and s["reference"] for s in sent)


def test_an_undecided_claim_is_graded_f_without_asking_the_judge(evaluated):
    env, run_id, settings = evaluated
    RawDb(env.engine).execute("DELETE FROM decisions WHERE claim_id = (SELECT claim_id FROM run_claims WHERE run_id = ? LIMIT 1)", (run_id,))
    stub = StubJudge()
    grade_evaluation(env.engine, run_id, settings, stub)
    rows = grades(env, run_id)
    f = [r for r in rows.values() if r["letter"] == "F"]
    assert len(f) == 1 and f[0]["score"] == 0 and f[0]["cost_usd"] == 0
    assert len(stub.calls) == 4


def test_a_claim_the_judge_could_not_grade_is_left_out_of_the_average(evaluated):
    env, run_id, settings = evaluated
    grade_evaluation(env.engine, run_id, settings, StubJudge(fail=True))
    rows = grades(env, run_id)
    assert all(r["score"] is None and r["error"] for r in rows.values())
    rows_for_measures = [{"decision_ok": True, "accurate": True, "mistake_cost": 0, "referred": False, "cost_usd": 0.0, "seconds": 1.0,
                          "score": s} for s in (2.0, None, 3.0)]
    assert measures(rows_for_measures, 3.0)["grade"] == 2.5  # the ungraded one is not a zero


def test_a_slow_judge_leaves_the_claims_ungraded_and_finishing_stays_quick(evaluated, monkeypatch):
    env, run_id, settings = evaluated
    monkeypatch.setattr(judge_module, "FINISH_DEADLINE_S", 0.5)  # 20 s in production
    started = time.monotonic()
    grade_evaluation(env.engine, run_id, settings, StubJudge(delay=5.0))
    assert time.monotonic() - started < 3
    assert all(r["score"] is None for r in grades(env, run_id).values())


def test_the_judges_cost_is_recorded_with_the_evaluation_apart_from_the_agents(evaluated):
    env, run_id, settings = evaluated
    before = run_totals(env.engine, run_id)["cost_usd"]
    grade_evaluation(env.engine, run_id, settings, StubJudge(answers(cost=0.0002)))
    assert run_totals(env.engine, run_id)["cost_usd"] == before  # the claim agents' cost is unchanged
    total = RawDb(env.engine).execute("SELECT SUM(cost_usd) FROM judge_grades WHERE run_id = ?", (run_id,)).fetchone()[0]
    assert total == pytest.approx(0.0002 * 5)


def test_with_model_name_the_judge_is_not_called(evaluated, monkeypatch):
    env, run_id, settings = evaluated
    monkeypatch.setenv("MODEL_NAME", "some/free-model")
    stub = StubJudge()
    grade_evaluation(env.engine, run_id, settings, stub)
    assert stub.calls == []
    assert all(r["score"] is None for r in grades(env, run_id).values())


def test_without_a_key_the_judge_is_not_called(evaluated):
    env, run_id, _ = evaluated
    stub = StubJudge()
    grade_evaluation(env.engine, run_id, Settings(_env_file=None, openrouter_api_key=None), stub)
    assert stub.calls == [] and all(r["score"] is None for r in grades(env, run_id).values())


def test_grading_a_dispatch_is_not_a_thing(db_path, data_dir):
    with agent_env(db_path, data_dir, ScriptedChatModel()) as env:
        from app.runs import start_dispatch

        run_id = start_dispatch(env.engine, 3, "m", None)
        stub = StubJudge()
        grade_evaluation(env.engine, run_id, SETTINGS, stub)
        assert stub.calls == []


def test_finishing_an_evaluation_grades_it_before_it_is_marked_finished(db_path, data_dir, monkeypatch):
    stub = StubJudge()
    monkeypatch.setattr(judge_module, "make_client", lambda settings: stub)
    with agent_env(db_path, data_dir, ScriptedChatModel(), openrouter_api_key="k") as env:
        run_id = start_evaluation(env.engine, env.ref, "evaluation", True, 5, "scripted", None)
        dispatch(env, run_id, 5)
        assert RawDb(env.engine).execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()[0] == "finished"
        assert RawDb(env.engine).execute("SELECT COUNT(*) FROM judge_grades WHERE run_id = ?", (run_id,)).fetchone()[0] == 5
    assert len(stub.calls) >= 1


def test_the_real_client_is_never_built_in_a_test(db_path, data_dir):
    with pytest.raises(AssertionError, match="tried to call the judge"):
        judge_module.make_client(SETTINGS)


@pytest.fixture
def backend(db_path, data_dir, monkeypatch):
    monkeypatch.setattr(judge_module, "make_client", lambda settings: StubJudge(answers(score=2.4, probabilities={"0": 0, "1": 0.1, "2": 0.6, "3": 0.3})))
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir, openrouter_api_key="k")
    with TestClient(create_service(settings, inline=True)) as service:
        with TestClient(create_app(settings, http=service, jwks=MINT), headers=MINT.header()) as client:
            yield client


def test_the_scoreboard_adds_the_average_grade_and_the_pages_show_each_claims_grade(backend):
    backend.post("/evaluations", json={"eval_set": "evaluation", "tiny": True, "workers": 5})
    listed = backend.get("/evaluations").json()[0]
    assert 0 <= listed["measures"]["grade"] <= 3 and listed["measures"]["judge_cost_usd"] > 0
    report = backend.get("/evaluations/1").json()
    row = next(r for r in report["claims"] if r["score"] is not None)
    assert row["letter"] == "B" and row["same_grounds"] >= 0 and row["same_section"] >= 0
    assert {"score", "letter", "same_grounds", "same_section"} <= set(row)
