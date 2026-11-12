"""Rounds, proposals, a version's scores, what the improver sees, and the rule that keeps or rolls back a version."""
import json
from pathlib import Path

import pytest

import app.judge as judge_module
from agent.dispatcher import dispatch
from agent.scripted import ScriptedChatModel
from app import configs, improvement
from app.auth import Handler
from app.config import Settings
from app.db import init_schema, make_engine
from app.evaluations import start_evaluation
from app.improvement import (AlreadyDecided, RoundOpen, RoundStopped, add_proposal, decide_proposal, estimate, get_round,
                             improver_view, keep_or_roll_back, needs_first_evaluations, record_verdict, round_spent,
                             start_round, stop_round, version_scores)
from app.reference import Reference
from app.seed import seed_claims
from app.settings_store import get_settings, models_allowed
from tests.fakes import RawDb, agent_env
from tests.test_judge import StubJudge, answers

DANA = Handler("HND-01", "Dana Whitfield", "Senior Claims Handler", "dana.whitfield@marvinsurance.example", True)
OLD = {"accuracy": 0.70, "grade": 1.50, "cost_per_claim": 0.0010, "seconds_per_claim": 20.0}
LAB = {"objectives": {"accuracy": 0.6, "grade": 1.0},
       "noise": {"accuracy": 0.1, "grade": 0.15, "cost_per_claim": 0.15, "seconds_per_claim": 0.15},
       "tolerance": {"accuracy": 0.1, "grade": 0.15, "cost_per_claim": 1.0, "seconds_per_claim": 0.25}}


@pytest.fixture
def engine(db_path, data_dir):
    engine = make_engine(Settings(_env_file=None, db_path=db_path), max_connections=2)
    init_schema(engine)
    seed_claims(engine, Reference(data_dir))
    yield engine
    engine.dispose()


@pytest.fixture
def env(db_path, data_dir, monkeypatch):
    monkeypatch.setattr(judge_module, "make_client", lambda settings: StubJudge(answers(score=2.0)))
    with agent_env(db_path, data_dir, ScriptedChatModel(), openrouter_api_key="k") as opened:
        yield opened


def whole(env, eval_set, version=1, round_id=1, tiny=False):
    """Work and grade one evaluation, the way a round's evaluations are."""
    run_id = start_evaluation(env.engine, env.ref, eval_set, tiny, 25, "scripted", None, config_version=version, round_id=round_id)
    dispatch(env, run_id, 25)
    return run_id


# --- the rule ---------------------------------------------------------------------------------------------

def test_a_version_is_kept_when_one_measure_improves_beyond_its_noise():
    assert keep_or_roll_back({**OLD, "accuracy": 0.82}, OLD, LAB) == (True, "accuracy improved")  # +0.12 > 0.1


def test_accuracy_and_the_grade_compare_as_differences():
    assert not keep_or_roll_back({**OLD, "accuracy": 0.79}, OLD, LAB)[0]  # +0.09 is within the noise
    assert keep_or_roll_back({**OLD, "grade": 1.70}, OLD, LAB) == (True, "grade improved")  # +0.20 > 0.15


def test_cost_and_time_compare_as_fractions_of_the_old_value():
    assert keep_or_roll_back({**OLD, "cost_per_claim": 0.0008}, OLD, LAB) == (True, "cost_per_claim improved")  # 20% cheaper > 15%
    assert not keep_or_roll_back({**OLD, "cost_per_claim": 0.00090}, OLD, LAB)[0]  # 10% is within the noise
    worse = keep_or_roll_back({**OLD, "accuracy": 0.82, "seconds_per_claim": 25.1}, OLD, LAB)  # 25.5% slower > 25%
    assert worse == (False, "seconds_per_claim got worse")
    assert keep_or_roll_back({**OLD, "accuracy": 0.82, "cost_per_claim": 0.0019}, OLD, LAB)[0]  # up to double is tolerated


def test_a_version_below_the_objectives_is_rolled_back():
    assert keep_or_roll_back({**OLD, "accuracy": 0.55, "grade": 3.0}, {**OLD, "accuracy": 0.3}, LAB) == (
        False, "it missed the objectives on accuracy")


def test_a_version_with_no_grade_is_rolled_back():
    assert keep_or_roll_back({**OLD, "accuracy": 0.9, "grade": None}, OLD, LAB) == (False, "it has no grade")


def test_a_version_whose_improvement_is_all_within_the_noise_is_rolled_back():
    assert keep_or_roll_back(dict(OLD), OLD, LAB) == (False, "no score improved by more than the noise")


def test_a_current_version_with_no_grade_still_lets_a_graded_one_be_judged_on_the_rest():
    assert keep_or_roll_back({**OLD, "accuracy": 0.9}, {**OLD, "grade": None}, LAB)[0]


# --- a version's scores and what is still owed ------------------------------------------------------------

def test_a_versions_scores_count_only_the_whole_set_evaluations_its_rounds_ran(env):
    whole(env, "evaluation")
    whole(env, "held_out")
    whole(env, "held_out")
    whole(env, "evaluation", round_id=None)  # started on the Scoreboard: not a round's
    whole(env, "held_out", tiny=True)  # a tiny set is not the whole set
    whole(env, "evaluation", version=2)  # another version's
    scores = version_scores(env.engine, 1)
    assert scores["evaluation"]["runs"] == 1 and scores["held-out"]["runs"] == 2
    assert 0 <= scores["held-out"]["accuracy"] <= 1 and scores["held-out"]["grade"] == 2.0
    assert version_scores(env.engine, 9) == {}


def test_what_a_version_still_owes_its_first_evaluations(env):
    assert needs_first_evaluations(env.engine, 1) == ["evaluation", "held_out", "held_out"]
    whole(env, "evaluation")
    assert needs_first_evaluations(env.engine, 1) == ["held_out", "held_out"]
    whole(env, "held_out")
    whole(env, "held_out")
    assert needs_first_evaluations(env.engine, 1) == []


# --- what the improver sees -------------------------------------------------------------------------------

def test_the_improvers_view_holds_nothing_from_the_held_out_set_but_its_scores(env):
    held_out = json.loads((Path(env.ref.data_dir) / "evals" / "held_out_decisions.json").read_text(encoding="utf-8"))
    whole(env, "evaluation")
    whole(env, "held_out")
    whole(env, "held_out")
    rid = start_round(env.engine, "HND-01", 1, {**get_settings(env.engine)})
    view = improver_view(env.engine, env.ref, env.settings, rid)
    text = json.dumps(view)
    for decision in held_out:
        assert decision["claim_id"] not in text and decision["reason"] not in text
    assert "E2-CLM-11" not in text and "CLM-11" not in text
    assert set(view["scores"]) == {"evaluation", "held-out"} and view["scores"]["held-out"]["runs"] == 2
    wrong, weakest = view["evaluation_set"]["wrong"], view["evaluation_set"]["explained_least_well"]
    assert all(c["handler"]["reason"] and c["handler"]["reference"] for c in wrong + weakest)
    assert all({"score", "letter", "same_grounds", "same_section"} <= set(c["judge"]) for c in wrong + weakest)
    assert len(weakest) <= 5 and all(c["agent"]["decision"] == c["handler"]["decision"] for c in weakest)


def test_the_improvers_view_has_the_rule_the_settings_the_spending_left_and_the_models_allowed(env):
    rid = start_round(env.engine, "HND-01", 1, {**get_settings(env.engine)})
    view = improver_view(env.engine, env.ref, env.settings, rid)
    assert view["objectives_on_the_held_out_set"] == LAB["objectives"] and view["noise"] == LAB["noise"]
    assert view["tolerance"] == LAB["tolerance"] and "meets the objectives" in view["rule"]
    assert view["spending_left_usd"] == pytest.approx(0.5) and view["current_version"]["version"] == 1
    assert set(view["models_allowed"]) == set(models_allowed(env.ref, env.settings))
    assert "scores" in view and view["earlier_rounds"] == []


def test_a_round_keeps_its_settings_when_they_change(engine):
    from app.settings_store import change_setting

    rid = start_round(engine, "HND-01", 1, get_settings(engine))
    change_setting(engine, "round_spending_limit", 9.0, DANA)
    assert json.loads(get_round(engine, rid)["settings"])["round_spending_limit"] == 0.5


# --- rounds and proposals ---------------------------------------------------------------------------------

def test_only_one_round_is_open_at_a_time(engine):
    first = start_round(engine, "HND-01", 1, get_settings(engine))
    with pytest.raises(RoundOpen):
        start_round(engine, "HND-01", 1, get_settings(engine))
    stop_round(engine, first, "HND-01")
    assert start_round(engine, "HND-01", 1, get_settings(engine)) == first + 1


def test_each_round_gets_a_thread_of_its_own(engine):
    a = start_round(engine, "HND-01", 1, get_settings(engine))
    stop_round(engine, a, "HND-01")
    b = start_round(engine, "HND-01", 1, get_settings(engine))
    ta, tb = get_round(engine, a)["thread_id"], get_round(engine, b)["thread_id"]
    assert ta != tb and ta.startswith(f"round-{a}-") and tb.startswith(f"round-{b}-")


CHANGE = {"instructions": "A new rule.", "model": "deepseek/deepseek-v4.1-flash", "reasoning_effort": "low"}


def test_a_proposal_changes_nothing_until_it_is_approved(engine):
    rid = start_round(engine, "HND-01", 1, get_settings(engine))
    pid = add_proposal(engine, rid, "change", CHANGE, "It would fix the misses")
    assert get_round(engine, rid)["stage"] == "awaiting_change"
    assert [v["version"] for v in configs.list_versions(engine, "config")] == [1]
    assert configs.current(engine, "config")["version"] == 1
    decide_proposal(engine, pid, True, "", DANA)
    assert [v["version"] for v in configs.list_versions(engine, "config")] == [1, 2]
    assert configs.current(engine, "config")["version"] == 1  # a version is current only when a round keeps it
    row = configs.get_version(engine, "config", 2)
    assert (row["proposed_by"], row["approved_by"], row["round_id"]) == ("improver", "HND-01", rid)
    assert get_round(engine, rid)["version_tried"] == 2


def test_a_rejection_records_the_handlers_reason_and_adds_no_version(engine):
    rid = start_round(engine, "HND-01", 1, get_settings(engine))
    pid = add_proposal(engine, rid, "change", CHANGE, "r")
    result = decide_proposal(engine, pid, False, "Too broad: name the rule's section.", DANA)
    assert result["status"] == "rejected" and result["decision_reason"] == "Too broad: name the rule's section."
    assert [v["version"] for v in configs.list_versions(engine, "config")] == [1]


def test_two_approvals_of_one_proposal_record_one(engine):
    rid = start_round(engine, "HND-01", 1, get_settings(engine))
    pid = add_proposal(engine, rid, "change", CHANGE, "r")
    decide_proposal(engine, pid, True, "", DANA)
    with pytest.raises(AlreadyDecided):
        decide_proposal(engine, pid, True, "", DANA)
    assert [v["version"] for v in configs.list_versions(engine, "config")] == [1, 2]  # not a version 3


def test_an_approved_recipe_becomes_the_current_recipe(engine):
    rid = start_round(engine, "HND-01", 1, get_settings(engine))
    pid = add_proposal(engine, rid, "recipe", {"recipe": "1. Look at the weakest explanations first."}, "r")
    assert get_round(engine, rid)["stage"] == "awaiting_recipe"
    decide_proposal(engine, pid, True, "", DANA)
    assert configs.current(engine, "recipe")["version"] == 2


def test_a_stopped_round_refuses_every_later_write(engine):
    rid = start_round(engine, "HND-01", 1, get_settings(engine))
    pid = add_proposal(engine, rid, "change", CHANGE, "r")
    stop_round(engine, rid, "HND-01")
    assert get_round(engine, rid)["stage"] == "stopped" and get_round(engine, rid)["stopped_by"] == "HND-01"
    with pytest.raises(RoundStopped):
        decide_proposal(engine, pid, True, "", DANA)
    with pytest.raises(RoundStopped):
        add_proposal(engine, rid, "recipe", {"recipe": "x"}, "r")
    with pytest.raises(RoundStopped):
        record_verdict(engine, rid, True, "why", OLD, OLD)
    assert [v["version"] for v in configs.list_versions(engine, "config")] == [1]


def test_a_round_stopped_before_its_verdict_leaves_the_current_version(engine):
    rid = start_round(engine, "HND-01", 1, get_settings(engine))
    stop_round(engine, rid, "HND-01")
    assert configs.current(engine, "config")["version"] == 1


def test_a_kept_version_becomes_current_and_stopping_afterwards_keeps_it(engine):
    rid = start_round(engine, "HND-01", 1, get_settings(engine))
    pid = add_proposal(engine, rid, "change", CHANGE, "r")
    decide_proposal(engine, pid, True, "", DANA)
    record_verdict(engine, rid, True, "accuracy improved", OLD, {**OLD, "accuracy": 0.9})
    assert configs.current(engine, "config")["version"] == 2
    stop_round(engine, rid, "HND-01")  # after the verdict
    assert configs.current(engine, "config")["version"] == 2
    assert get_round(engine, rid)["verdict"] == "kept"


def test_a_rolled_back_version_leaves_the_current_one(engine):
    rid = start_round(engine, "HND-01", 1, get_settings(engine))
    decide_proposal(engine, add_proposal(engine, rid, "change", CHANGE, "r"), True, "", DANA)
    record_verdict(engine, rid, False, "accuracy got worse", OLD, OLD)
    assert configs.current(engine, "config")["version"] == 1 and get_round(engine, rid)["verdict"] == "rolled_back"


# --- the spending -----------------------------------------------------------------------------------------

def test_what_a_round_spent_counts_the_improver_every_evaluation_and_the_judge(env):
    rid = start_round(env.engine, "HND-01", 1, get_settings(env.engine))
    raw = RawDb(env.engine)
    raw.execute("INSERT INTO model_calls (claim_id, run_id, round_id, model, input_tokens, output_tokens, reasoning_tokens, cost_usd) "
                "VALUES ('', NULL, ?, 'improver', 1, 1, 0, 0.01)", (rid,))
    run_id = start_evaluation(env.engine, env.ref, "evaluation", True, 5, "scripted", None, config_version=1, round_id=rid)
    dispatch(env, run_id, 5)  # the scripted model's calls cost 0.0005 each in the stub; the judge 0.0001 a claim
    spent = round_spent(env.engine, rid)
    claim_agents = RawDb(env.engine).execute("SELECT SUM(cost_usd) FROM model_calls WHERE run_id = ?", (run_id,)).fetchone()[0] or 0
    judge = RawDb(env.engine).execute("SELECT SUM(cost_usd) FROM judge_grades WHERE run_id = ?", (run_id,)).fetchone()[0] or 0
    assert spent == pytest.approx(0.01 + claim_agents + judge) and spent > 0.01


def test_the_estimate_is_the_claims_times_the_highest_cost_per_claim_seen_for_the_model(env):
    run_id = whole(env, "evaluation", round_id=1)
    assert run_id
    raw = RawDb(env.engine)
    raw.execute("UPDATE runs SET model = 'deepseek/deepseek-v4.1-flash' WHERE run_id = ?", (run_id,))
    raw.execute("UPDATE model_calls SET cost_usd = 0.002 WHERE run_id = ?", (run_id,))
    raw.execute("UPDATE judge_grades SET cost_usd = 0 WHERE run_id = ?", (run_id,))
    models = models_allowed(env.ref, env.settings)
    one = estimate(env.engine, "deepseek/deepseek-v4.1-flash", 1, models, "deepseek/deepseek-v4.1-flash")
    assert one >= 0.002  # at least one call's worth, and the highest claim's total
    assert estimate(env.engine, "deepseek/deepseek-v4.1-flash", 10, models, "deepseek/deepseek-v4.1-flash") == pytest.approx(one * 10)


def test_the_estimate_for_a_model_with_no_runs_scales_the_current_models_by_the_larger_price_ratio(env):
    whole(env, "evaluation", round_id=1)
    raw = RawDb(env.engine)
    raw.execute("UPDATE runs SET model = 'deepseek/deepseek-v4.1-flash'")
    models = models_allowed(env.ref, env.settings)
    base = estimate(env.engine, "deepseek/deepseek-v4.1-flash", 10, models, "deepseek/deepseek-v4.1-flash")
    # Gemini: input 0.30 / 0.30 = 1.0, output 2.50 / 1.20 = 2.08: the larger ratio scales the estimate
    scaled = estimate(env.engine, "google/gemini-3.5-flash-lite", 10, models, "deepseek/deepseek-v4.1-flash")
    assert scaled == pytest.approx(base * (2.50 / 1.20))
    cheaper = estimate(env.engine, "openai/gpt-6-luna", 10, models, "deepseek/deepseek-v4.1-flash")  # 0.10/0.30 and 0.50/1.20
    assert cheaper == pytest.approx(base * max(0.10 / 0.30, 0.50 / 1.20))
