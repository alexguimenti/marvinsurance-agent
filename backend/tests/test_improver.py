"""The improver: a round's first evaluations, its pauses for a handler, what comes back from a rejection, the spending
limit, and a stopped round. The claim agent runs on a stub and the judge on a stub: nothing here calls a model."""
import ast
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import agent.dispatcher as dispatcher_module
import agent.improver as improver
import agent.service as service_module
import app.judge as judge_module
from agent.scripted import ScriptedChatModel
from agent.service import create_service
from agent.tracing import improver_trace_config, trace_config
from app import configs, improvement
from app.api import create_app
from app.auth import Handler
from app.config import Settings
from app.settings_store import change_setting, get_settings
from tests.fakes import MINT, FakeToolModel, RawDb, agent_env, credit_error, script, tool_call
from tests.test_judge import StubJudge, answers

DANA = Handler("HND-01", "Dana Whitfield", "Senior Claims Handler", "dana.whitfield@marvinsurance.example", True)
OK_MODEL = "deepseek/deepseek-v4.1-flash"


def change(instructions="New rules.\n", model=OK_MODEL, effort="low", reason="It fixes the misses", call_id="c1"):
    return tool_call("propose_change", {"instructions": instructions, "model": model, "reasoning_effort": effort, "reason": reason}, call_id)


def recipe(text="1. Read.\n2. Propose.", call_id="r1"):
    return tool_call("propose_recipe", {"recipe": text, "reason": "What this round showed"}, call_id)


@pytest.fixture(autouse=True)
def stub_judge(monkeypatch):
    monkeypatch.setattr(judge_module, "make_client", lambda settings: StubJudge(answers(score=2.0)))


def open_env(db_path, data_dir, improver_model=None):
    return agent_env(db_path, data_dir, ScriptedChatModel(), improver_model=improver_model, openrouter_api_key="k")


@pytest.fixture
def no_first_evaluations(monkeypatch):
    """Skip the evaluations a round owes first, for the tests that are about the improver's pauses."""
    monkeypatch.setattr(improver, "first_evaluations", lambda env, round_id: True)


def waiting(env, round_id):
    return improvement.waiting_proposal(env.engine, round_id)


def run_round(env, model):
    env.improver_model = model
    round_id = improver.begin(env, "HND-01")
    improver.work_round(env, round_id)
    return round_id


# --- a round's pauses ---------------------------------------------------------------------------------------

def test_a_proposal_changes_nothing_until_it_is_approved(db_path, data_dir, no_first_evaluations):
    with open_env(db_path, data_dir) as env:
        round_id = run_round(env, script(change()))
        assert improvement.get_round(env.engine, round_id)["stage"] == "awaiting_change"
        assert [v["version"] for v in configs.list_versions(env.engine, "config")] == [1]
        proposal = waiting(env, round_id)
        assert proposal["kind"] == "change" and proposal["reason"] == "It fixes the misses"


def test_an_approved_change_becomes_a_version_and_the_improver_carries_on(db_path, data_dir, no_first_evaluations):
    with open_env(db_path, data_dir) as env:
        model = script(change(), AIMessage(content="Done."))
        round_id = run_round(env, model)
        improver.record_decision(env, round_id, waiting(env, round_id)["id"], True, "", DANA)
        assert [v["version"] for v in configs.list_versions(env.engine, "config")] == [1, 2]
        improver.resume_round(env, round_id, True, "")
        assert improvement.get_round(env.engine, round_id)["stage"] == "finished"
        tool_text = " ".join(str(m.content) for m in model.seen[-1] if m.type == "tool")
        assert "Saved as version 2" in tool_text


def test_a_rejections_reason_reaches_the_improver_which_may_propose_again(db_path, data_dir, no_first_evaluations):
    with open_env(db_path, data_dir) as env:
        model = script(change(call_id="c1"), change(instructions="Narrower rules.\n", call_id="c2"))
        round_id = run_round(env, model)
        first = waiting(env, round_id)["id"]
        improver.record_decision(env, round_id, first, False, "Too broad: name the section.", DANA)
        improver.resume_round(env, round_id, False, "Too broad: name the section.")
        assert "Too broad: name the section." in " ".join(str(m.content) for m in model.seen[-1])
        second = waiting(env, round_id)
        assert second["id"] != first and improvement.get_round(env.engine, round_id)["stage"] == "awaiting_change"
        assert [v["version"] for v in configs.list_versions(env.engine, "config")] == [1]  # still nothing approved


def test_a_proposal_outside_the_models_allowed_goes_back_without_reaching_a_handler(db_path, data_dir, no_first_evaluations):
    with open_env(db_path, data_dir) as env:
        model = script(change(model="some/unlisted-model", call_id="c1"), change(call_id="c2"))
        round_id = run_round(env, model)
        assert "isn't allowed" in " ".join(str(m.content) for m in model.seen[-1])  # the improver was told
        proposals = improvement.list_proposals(env.engine, round_id)
        assert len(proposals) == 1 and proposals[0]["status"] == "waiting"  # only the allowed one reached a handler
        assert "some/unlisted-model" not in proposals[0]["payload"]


def test_a_reasoning_effort_the_model_does_not_allow_goes_back_too(db_path, data_dir, no_first_evaluations):
    with open_env(db_path, data_dir) as env:
        model = script(change(effort="high", call_id="c1"), change(call_id="c2"))
        round_id = run_round(env, model)
        assert len(improvement.list_proposals(env.engine, round_id)) == 1


def test_a_round_that_proposes_nothing_finishes_with_no_version_tried(db_path, data_dir, no_first_evaluations):
    with open_env(db_path, data_dir) as env:
        round_id = run_round(env, script(AIMessage(content="Nothing to improve.")))
        row = improvement.get_round(env.engine, round_id)
        assert (row["stage"], row["version_tried"], row["open"]) == ("finished", None, False)


def test_a_key_out_of_credit_ends_the_round_saying_so(db_path, data_dir, no_first_evaluations):
    class OutOfCredit(FakeToolModel):
        def _generate(self, *args, **kwargs):
            raise credit_error()

    with open_env(db_path, data_dir) as env:
        round_id = run_round(env, OutOfCredit(messages=iter([]), seen=[]))
        row = improvement.get_round(env.engine, round_id)
        assert row["open"] is False and "credit limit" in row["error"] and "402" not in row["error"]


def test_the_improvers_calls_are_recorded_with_its_round(db_path, data_dir, no_first_evaluations):
    with open_env(db_path, data_dir) as env:
        round_id = run_round(env, script(AIMessage(content="Nothing to improve.")))
        calls = RawDb(env.engine).execute("SELECT run_id, claim_id FROM model_calls WHERE round_id = ?", (round_id,)).fetchall()
        assert len(calls) == 1 and calls[0]["run_id"] is None


def test_a_round_waiting_for_a_handler_survives_a_restart(db_path, data_dir, no_first_evaluations):
    with open_env(db_path, data_dir) as before:
        round_id = run_round(before, script(change()))
        proposal_id = waiting(before, round_id)["id"]
    with open_env(db_path, data_dir, improver_model=script(AIMessage(content="Done."))) as after:  # a new process
        assert improvement.get_round(after.engine, round_id)["stage"] == "awaiting_change"
        improver.record_decision(after, round_id, proposal_id, True, "", DANA)
        improver.resume_round(after, round_id, True, "")
        assert improvement.get_round(after.engine, round_id)["stage"] == "finished"
        assert configs.get_version(after.engine, "config", 2)["approved_by"] == "HND-01"


def test_the_service_marks_a_round_in_the_middle_of_its_work_stopped_when_it_starts(db_path, data_dir):
    with open_env(db_path, data_dir) as env:
        working = improvement.start_round(env.engine, "HND-01", 1, get_settings(env.engine))
        improvement.set_stage(env.engine, working, "evaluating", "2 of 3")
        improvement.stop_round(env.engine, working, None)  # (one round at a time: end it to open the next)
        waiting_round = improvement.start_round(env.engine, "HND-01", 1, get_settings(env.engine))
        improvement.add_proposal(env.engine, waiting_round, "change", {"instructions": "x", "model": OK_MODEL, "reasoning_effort": "low"}, "r")
        assert improvement.stop_working_rounds(env.engine) == []
        assert improvement.get_round(env.engine, waiting_round)["stage"] == "awaiting_change"
        improvement.set_stage(env.engine, waiting_round, "evaluating")
        assert improvement.stop_working_rounds(env.engine) == [waiting_round]
        assert improvement.get_round(env.engine, waiting_round)["stage"] == "stopped"


# --- the evaluations and the spending limit -----------------------------------------------------------------

def a_version_tried(env, limit):
    """A round whose approved version 2 is waiting to be evaluated, working to this spending limit."""
    change_setting(env.engine, "round_spending_limit", limit, DANA)
    round_id = improvement.start_round(env.engine, "HND-01", 1, get_settings(env.engine))
    pid = improvement.add_proposal(env.engine, round_id, "change", {"instructions": "New rules.\n", "model": OK_MODEL, "reasoning_effort": "low"}, "r")
    improvement.decide_proposal(env.engine, pid, True, "", DANA)
    return round_id


def test_a_round_runs_the_first_evaluations_it_is_owed(db_path, data_dir):
    with open_env(db_path, data_dir, improver_model=script(AIMessage(content="Nothing to improve."))) as env:
        round_id = improver.begin(env, "HND-01")
        improver.work_round(env, round_id)
        done = RawDb(env.engine).execute(
            "SELECT eval_set, config_version, round_id, status FROM runs WHERE round_id = ? ORDER BY run_id", (round_id,)).fetchall()
        assert [(r["eval_set"], r["config_version"], r["status"]) for r in done] == [
            ("evaluation", 1, "finished"), ("held_out", 1, "finished"), ("held_out", 1, "finished")]
        assert improvement.needs_first_evaluations(env.engine, 1) == []
        assert improvement.get_round(env.engine, round_id)["stage"] == "finished"


def test_an_evaluation_that_would_go_over_the_limit_is_refused_and_the_version_rolled_back(db_path, data_dir):
    with open_env(db_path, data_dir) as env:
        round_id = a_version_tried(env, 0.0001)
        text = improver.evaluate_new_version(env, round_id)
        assert text.startswith("Refused") and "could not afford" in text
        row = improvement.get_round(env.engine, round_id)
        assert row["verdict"] == "rolled_back" and configs.current(env.engine, "config")["version"] == 1
        assert RawDb(env.engine).execute("SELECT COUNT(*) FROM runs WHERE round_id = ?", (round_id,)).fetchone()[0] == 0


def test_an_evaluation_cut_short_by_the_limit_shows_as_stopped_and_the_version_is_rolled_back(db_path, data_dir, monkeypatch):
    with open_env(db_path, data_dir) as env:
        round_id = a_version_tried(env, 0.5)
        RawDb(env.engine).execute(  # the round has already spent its whole limit
            "INSERT INTO model_calls (claim_id, run_id, round_id, model, input_tokens, output_tokens, reasoning_tokens, cost_usd) "
            "VALUES ('', NULL, ?, 'improver', 1, 1, 0, 0.5)", (round_id,))
        monkeypatch.setattr(improver, "_affordable", lambda env, round_id, version, claims: (True, 0.0, 1.0))  # it looked affordable
        text = improver.evaluate_new_version(env, round_id)
        assert "stopped by the round's spending limit" in text
        run = RawDb(env.engine).execute("SELECT status, error FROM runs WHERE round_id = ?", (round_id,)).fetchone()
        assert run["status"] == "stopped" and "spending limit" in run["error"]
        assert improvement.get_round(env.engine, round_id)["verdict"] == "rolled_back"


def test_a_stopped_round_starts_no_more_claims(db_path, data_dir, monkeypatch):
    with open_env(db_path, data_dir) as env:
        round_id = a_version_tried(env, 0.5)
        improvement.stop_round(env.engine, round_id, "HND-01")
        status = improver.run_round_evaluation(env, round_id, 2, "evaluation")
        assert status == "stopped"
        assert RawDb(env.engine).execute("SELECT COUNT(*) FROM claims WHERE status != 'waiting' AND evaluation_id IS NOT NULL").fetchone()[0] == 0


def test_a_whole_new_version_is_evaluated_and_kept_or_rolled_back_by_the_rule(db_path, data_dir, no_first_evaluations):
    with open_env(db_path, data_dir) as env:
        round_id = a_version_tried(env, 0.5)
        text = improver.evaluate_new_version(env, round_id)  # the scripted claim agent is not accurate enough for the objectives
        row = improvement.get_round(env.engine, round_id)
        assert row["verdict"] == "rolled_back" and "it missed the objectives on accuracy" in text  # the scripted agent is far below the floor
        evaluated = RawDb(env.engine).execute("SELECT eval_set FROM runs WHERE round_id = ? ORDER BY run_id", (round_id,)).fetchall()
        assert [r["eval_set"] for r in evaluated] == ["evaluation", "held_out", "held_out"]
        assert configs.current(env.engine, "config")["version"] == 1


# --- tracing ------------------------------------------------------------------------------------------------

def test_a_rounds_work_goes_in_one_session_and_its_claims_carry_its_tag():
    config = improver_trace_config(7, ["cb"])
    assert config["metadata"]["langfuse_session_id"] == "round-7" and config["callbacks"][0] == "cb"
    assert "round-7" in trace_config("CLM-1001", "CLM-1001-1", "m", run_id=3, round_id=7)["metadata"]["langfuse_tags"]


# --- the claim agent cannot reach the scores or the handlers' decisions -------------------------------------

AGENT = Path(__file__).resolve().parents[1] / "agent"
CLAIM_AGENT = ("graph.py", "tools.py", "worker.py", "dispatcher.py", "prompts.py", "models.py", "events.py", "checkpoints.py", "scripted.py")
FORBIDDEN = ("app.scoring", "app.improvement", "app.judge", "agent.improver", "agent.improver_backend")


def imports_of(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found |= {f"{node.module}.{a.name}" for a in node.names}
        elif isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
    return found


@pytest.mark.parametrize("name", CLAIM_AGENT)
def test_the_claim_agent_still_cannot_reach_the_scores_or_the_decisions(name):
    imported = imports_of(AGENT / name)
    assert not [i for i in imported if i.startswith(FORBIDDEN)], f"{name} imports {sorted(imported)}"
    text = (AGENT / name).read_text(encoding="utf-8")
    assert "answer_key" not in text and "evaluation_set_review" not in text


def test_only_the_improvers_own_module_reaches_the_improvement_records():
    assert [i for i in imports_of(AGENT / "improver.py") if i.startswith(("app.scoring", "app.improvement", "app.judge", "app.db"))] == []


# --- the routes -------------------------------------------------------------------------------------------

@pytest.fixture
def backend(db_path, data_dir):
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir, openrouter_api_key="k")
    with TestClient(create_service(settings, inline=True)) as svc:
        with TestClient(create_app(settings, http=svc, jwks=MINT)) as client:
            yield client, svc


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/rounds", None), ("POST", "/rounds/1/stop", None),
    ("POST", "/rounds/1/decision", {"proposal_id": 1, "approve": True, "reason": ""})])
def test_every_round_action_needs_a_sign_in(backend, method, path, body):
    client, _ = backend
    assert client.request(method, path, json=body).status_code == 401


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/rounds", None), ("POST", "/rounds/1/stop", None),
    ("POST", "/rounds/1/decision", {"proposal_id": 1, "approve": True, "reason": ""})])
def test_only_a_senior_handler_can_start_stop_or_decide(backend, method, path, body):
    client, _ = backend
    assert client.request(method, path, json=body, headers=MINT.header("HND-02")).status_code == 403  # Marcus
    assert client.request(method, path, json=body, headers=MINT.header("HND-03")).status_code == 403  # Priya


def test_a_whole_round_through_the_routes(backend):
    client, _ = backend
    dana = MINT.header("HND-01")
    started = client.post("/rounds", headers=dana)  # inline: the first evaluations and the improver run before it answers
    assert started.status_code == 202
    round_id = started.json()["round_id"]
    view = client.get(f"/rounds/{round_id}").json()
    assert view["stage"] == "awaiting_change" and view["started_by_name"] == "Dana Whitfield"
    assert view["waiting"]["kind"] == "change" and any(l["kind"] == "add" for l in view["waiting"]["lines"])
    assert view["waiting"]["model"]["before"] == OK_MODEL
    assert client.get("/configuration").json()["current"]["version"] == 1  # nothing changed yet

    # rejecting it sends the handler's reason back, and the scripted improver proposes again
    first = view["waiting"]["id"]
    assert client.post(f"/rounds/{round_id}/decision", json={"proposal_id": first, "approve": False, "reason": "Be more specific"},
                       headers=dana).status_code == 202
    again = client.get(f"/rounds/{round_id}").json()
    assert again["waiting"]["id"] != first and again["stage"] == "awaiting_change"
    assert any(p["status"] == "rejected" and p["decision_reason"] == "Be more specific" and p["decided_by_name"] == "Dana Whitfield"
               for p in again["proposals"])

    # approving it has the new version evaluated, and the rule decides; then a recipe waits
    assert client.post(f"/rounds/{round_id}/decision", json={"proposal_id": again["waiting"]["id"], "approve": True, "reason": ""},
                       headers=dana).status_code == 202
    done = client.get(f"/rounds/{round_id}").json()
    assert done["version_tried"] == 2 and done["verdict"] in ("kept", "rolled_back") and done["why"]
    assert done["stage"] == "awaiting_recipe" and done["waiting"]["kind"] == "recipe"
    assert done["before"] is not None and done["after"] is not None and done["spent_usd"] > 0

    assert client.post(f"/rounds/{round_id}/decision", json={"proposal_id": done["waiting"]["id"], "approve": True, "reason": ""},
                       headers=dana).status_code == 202
    finished = client.get(f"/rounds/{round_id}").json()
    assert finished["stage"] == "finished" and finished["open"] is False
    assert client.get("/configuration").json()["current_recipe"] == 2
    assert client.post(f"/rounds/{round_id}/stop", headers=dana).status_code == 409  # already ended
    assert [r["round_id"] for r in client.get("/rounds").json()] == [round_id]


def test_a_second_round_is_refused_while_one_is_open(backend):
    client, _ = backend
    dana = MINT.header("HND-01")
    first = client.post("/rounds", headers=dana).json()["round_id"]
    refused = client.post("/rounds", headers=dana)
    assert refused.status_code == 409 and f"round {first}" in refused.json()["detail"]


def test_a_stopped_round_refuses_a_decision_and_changes_nothing(backend):
    client, _ = backend
    dana = MINT.header("HND-01")
    round_id = client.post("/rounds", headers=dana).json()["round_id"]
    proposal = client.get(f"/rounds/{round_id}").json()["waiting"]["id"]
    assert client.post(f"/rounds/{round_id}/stop", headers=dana).status_code == 200
    answer = client.post(f"/rounds/{round_id}/decision", json={"proposal_id": proposal, "approve": True, "reason": ""}, headers=dana)
    assert answer.status_code == 409
    assert [v["version"] for v in client.get("/configuration").json()["versions"]] == [1]
    assert client.get(f"/rounds/{round_id}").json()["stage"] == "stopped"
    assert client.post("/rounds/99/stop", headers=dana).status_code == 404


def test_a_decision_is_refused_while_a_dispatch_runs_and_nothing_is_recorded(db_path, data_dir, monkeypatch):
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir, openrouter_api_key="k")
    release, started = threading.Event(), threading.Event()

    def slow_dispatch(env, run_id, workers, *args, **kwargs):
        started.set()
        release.wait(10)
        dispatcher_module.runs.finish_run(env.engine, run_id)

    monkeypatch.setattr(service_module, "dispatch", slow_dispatch)
    with TestClient(create_service(settings)) as svc:  # real threads: the dispatch is running while we ask
        engine = RawDb(service_module.make_engine(settings, 1)).engine
        round_id = improvement.start_round(engine, "HND-01", 1, get_settings(engine))
        pid = improvement.add_proposal(engine, round_id, "change", {"instructions": "x", "model": OK_MODEL, "reasoning_effort": "low"}, "r")
        assert svc.post("/dispatches", json={"workers": 3}).status_code == 202
        assert started.wait(10)
        answer = svc.post(f"/rounds/{round_id}/decision", json={"proposal_id": pid, "approve": True, "reason": "", "handler_id": "HND-01"})
        assert answer.status_code == 409 and "a dispatch is already running" in answer.json()["detail"]
        assert improvement.get_proposal(engine, pid)["status"] == "waiting"  # nothing recorded
        assert [v["version"] for v in configs.list_versions(engine, "config")] == [1]
        release.set()


def test_dispatches_and_evaluations_can_run_while_a_round_waits_for_a_handler(backend):
    client, _ = backend
    dana = MINT.header("HND-01")
    round_id = client.post("/rounds", headers=dana).json()["round_id"]
    assert client.get(f"/rounds/{round_id}").json()["stage"] == "awaiting_change"
    assert client.post("/dispatches", json={"workers": 5}, headers=dana).status_code == 202
    assert client.post("/evaluations", json={"eval_set": "evaluation", "tiny": True, "workers": 3}, headers=dana).status_code == 202
