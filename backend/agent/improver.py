"""The improver, the agent's sixth part: an agent that follows a recipe to improve the claim agent. It reads the
results, proposes a change, and has the new version evaluated; a handler approves every proposal and code keeps or rolls
back the version. It has no model-written verdicts and no way to change the settings, the models allowed or the judge.

A round is worked in stretches that end where the improver waits for a handler (a proposal) or finishes. Between
stretches nothing runs and nothing is held, so dispatches and evaluations can run while a handler decides. The improver
pauses with LangChain's HumanInTheLoopMiddleware on a checkpointer of its own, so a round waiting for approval survives
a restart."""
import contextvars
import json
import logging
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, ModelCallLimitMiddleware
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.types import Command

from agent import improver_backend as backend
from agent.dispatcher import CLAIM_RETRY, dispatch
from agent.events import call_usage
from agent.tracing import improver_trace_config
from agent.worker import AgentEnv

log = logging.getLogger(__name__)

MAX_IMPROVER_MODEL_CALLS = 30  # for the whole round, as the claim agent's are for a claim
KINDS = {"propose_change": "change", "propose_recipe": "recipe"}
HERE = Path(__file__).parent


def instructions_for(recipe: str) -> str:
    """The improver's instructions, written as goals, around the recipe it follows."""
    return (HERE / "improver_instructions.md").read_text(encoding="utf-8").replace("{recipe}", recipe)


def make_model(env: AgentEnv):
    """The model the improver runs on: a test's own, the scripted one, or the default model with parallel tool calls
    off, so that a reply never holds two proposals."""
    if env.improver_model is not None:
        return env.improver_model, "improver-stub"
    if env.settings.agent_provider == "scripted":
        from agent.scripted import ScriptedImproverModel

        return ScriptedImproverModel(), "scripted"
    from agent.models import chat_model
    from app.configs import default_model_and_effort

    model, effort = default_model_and_effort()
    return chat_model(env.settings, model, effort, parallel_tool_calls=False), model


class RecordCost(BaseCallbackHandler):
    """Every model reply of the improver is recorded with its round, with OpenRouter's cost as for the claim agent."""

    def __init__(self, env: AgentEnv, round_id: int, model_id: str):
        self.env, self.round_id, self.model_id = env, round_id, model_id

    def on_llm_end(self, response, **kwargs: Any) -> None:
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                if message is not None:
                    backend.record_model_call(self.env.engine, "", None, self.model_id, call_usage(message), round_id=self.round_id)


# --- the evaluations a round asks for ---------------------------------------------------------------------

def _settings_of(env: AgentEnv, round_id: int) -> dict:
    return json.loads(backend.get_round(env.engine, round_id)["settings"])


def _may_start_a_claim(env: AgentEnv, round_id: int, limit: float):
    """No new claim starts once the round has spent its limit, or has ended."""

    def allowed() -> bool:
        return bool(backend.get_round(env.engine, round_id)["open"]) and backend.round_spent(env.engine, round_id) < limit

    return allowed


def _cost_of(env: AgentEnv, version: dict, claims: int) -> float:
    models = backend.models_allowed(env.ref, env.settings)
    current = backend.current_version(env.engine, "config")
    return backend.estimate(env.engine, env.effective(version)[0], claims, models, env.effective(current)[0])


def run_round_evaluation(env: AgentEnv, round_id: int, version: int, eval_set: str) -> str:
    """Work one whole-set evaluation of a version for the round, graded as it finishes. Returns the run's status."""
    settings = _settings_of(env, round_id)
    config = backend.get_version(env.engine, "config", version)
    model, effort = env.effective(config)
    run_id = backend.start_evaluation(env.engine, env.ref, eval_set, False, settings["agents_at_once"], model, effort,
                                      config_version=version, round_id=round_id)
    allowed = _may_start_a_claim(env, round_id, settings["round_spending_limit"])
    # In a context of its own, as the harness runs each claim graph: a graph built without a checkpointer would
    # otherwise save into the thread of whoever called it, which here is the improver's.
    contextvars.Context().run(dispatch, env, run_id, settings["agents_at_once"], CLAIM_RETRY, allowed)
    return backend.run_status(env.engine, run_id)


def _affordable(env: AgentEnv, round_id: int, version: dict, claims: int) -> tuple[bool, float, float]:
    settings = _settings_of(env, round_id)
    left = settings["round_spending_limit"] - backend.round_spent(env.engine, round_id)
    cost = _cost_of(env, version, claims)
    return cost <= left, cost, left


def first_evaluations(env: AgentEnv, round_id: int) -> bool:
    """Run what the current version still owes: one evaluation, and two held-out. False if the round could not afford it."""
    current = backend.current_version(env.engine, "config")
    owed = backend.needs_first_evaluations(env.engine, current["version"])
    if not owed:
        return True
    claims = sum(backend.set_size(env.engine, s) for s in owed)
    fits, cost, left = _affordable(env, round_id, current, claims)
    if not fits:
        backend.finish_round(env.engine, round_id, error=(
            f"The round could not afford the {len(owed)} evaluations version {current['version']} still needed: "
            f"about ${cost:.2f}, with ${left:.2f} of its spending limit left."))
        return False
    for number, eval_set in enumerate(owed, 1):
        backend.set_stage(env.engine, round_id, "first_evaluations", f"{number} of {len(owed)}")
        run_round_evaluation(env, round_id, current["version"], eval_set)
    return True


# --- the improver's tools ---------------------------------------------------------------------------------

def make_tools(env: AgentEnv, round_id: int):
    """The lab's four tools, with the lab's names, since the recipe names them."""

    @tool
    def read_results() -> str:
        """Read the current version of the claim agent's configuration, its scores, its mistakes and weakest explanations on the evaluation set, the earlier rounds, the objectives, the spending left and the models allowed."""
        return json.dumps(backend.improver_view(env.engine, env.ref, env.settings, round_id), indent=2, default=str)

    @tool(parse_docstring=True)
    def propose_change(instructions: str, model: str, reasoning_effort: str, reason: str) -> str:
        """Propose a new version of the claim agent's configuration. A person reviews it before it is saved.

        Args:
            instructions: The claim agent's complete instructions
            model: One of the models allowed
            reasoning_effort: A reasoning level allowed for that model
            reason: Why this change should improve the scores
        """
        # Runs only once a handler has approved: the service recorded the approval and the version before resuming.
        row = backend.get_round(env.engine, round_id)
        return f"Saved as version {row['version_tried']}. Run the evaluation next."

    @tool
    def run_evaluation() -> str:
        """Run the newest version once on the evaluation set and twice on the held-out set, then keep it or roll it back."""
        return evaluate_new_version(env, round_id)

    @tool(parse_docstring=True)
    def propose_recipe(recipe: str, reason: str) -> str:
        """Propose a new version of your recipe for improving the claim agent. A person reviews it before it is saved.

        Args:
            recipe: The complete new recipe
            reason: What this round showed, and why the new recipe should lead to better changes
        """
        return f"Saved as recipe version {backend.current_version(env.engine, 'recipe')['version']}."

    return [read_results, propose_change, run_evaluation, propose_recipe]


def evaluate_new_version(env: AgentEnv, round_id: int) -> str:
    """Evaluate the version a handler approved, once on the evaluation set and twice held-out, and let the rule keep it or
    roll it back. A version the round cannot afford to evaluate, or whose evaluations were stopped, is rolled back."""
    row = backend.get_round(env.engine, round_id)
    version = row["version_tried"]
    if version is None or row["verdict"] is not None:
        return "There's no new version to evaluate. Propose a change first."
    settings = _settings_of(env, round_id)
    config = backend.get_version(env.engine, "config", version)
    current = backend.current_version(env.engine, "config")
    before = backend.version_scores(env.engine, current["version"]).get("held-out", {})
    claims = backend.set_size(env.engine, "evaluation") + 2 * backend.set_size(env.engine, "held_out")
    fits, cost, left = _affordable(env, round_id, config, claims)

    def settle(kept: bool, why: str, after: dict) -> str:
        backend.record_verdict(env.engine, round_id, kept, why, before, after)
        scores = {"held_out_before": before, "held_out_after": after}
        return f"{'Kept' if kept else 'Rolled back'} version {version}: {why}. Version {backend.current_version(env.engine, 'config')['version']} is now the current version.\n{json.dumps(scores, indent=2)}"

    if not fits:
        backend.set_stage(env.engine, round_id, "evaluating")
        return "Refused: " + settle(False, f"the round could not afford to evaluate it: about ${cost:.2f} against ${left:.2f} left", {})
    for number, eval_set in enumerate(("evaluation", "held_out", "held_out"), 1):
        backend.set_stage(env.engine, round_id, "evaluating", f"{number} of 3")
        if run_round_evaluation(env, round_id, version, eval_set) != "finished":
            return settle(False, "an evaluation was stopped by the round's spending limit", {})
    after = backend.version_scores(env.engine, version).get("held-out", {})
    kept, why = backend.keep_or_roll_back(after, before, settings)
    return settle(kept, why, after)


# --- the improver itself ----------------------------------------------------------------------------------

def _agent(env: AgentEnv, round_id: int, saver):
    row = backend.get_round(env.engine, round_id)
    recipe = backend.get_version(env.engine, "recipe", row["recipe_version"])["recipe"]
    model, _ = make_model(env)
    approval = HumanInTheLoopMiddleware(interrupt_on={name: {"allowed_decisions": ["approve", "reject"]} for name in KINDS})
    return create_agent(model=model, tools=make_tools(env, round_id), system_prompt=instructions_for(recipe),
                        middleware=[approval, ModelCallLimitMiddleware(thread_limit=MAX_IMPROVER_MODEL_CALLS, exit_behavior="end")],
                        checkpointer=saver)


def _invoke(env: AgentEnv, round_id: int, graph_input):
    row = backend.get_round(env.engine, round_id)
    _, model_id = make_model(env)
    config = {"configurable": {"thread_id": row["thread_id"]}, "recursion_limit": 100,
              **improver_trace_config(round_id, [RecordCost(env, round_id, model_id)])}
    with env.checkpoints.saver() as saver:
        # A fresh context, as for the claim graphs: nothing of the caller's leaks into the improver's traces or threads.
        return contextvars.Context().run(_agent(env, round_id, saver).invoke, graph_input, config, durability="sync")


def _problem(env: AgentEnv, request: dict) -> str | None:
    """Why a proposal cannot go to a handler, or None. A model outside the models allowed goes back to the improver."""
    args = request["args"]
    if request["name"] == "propose_change":
        allowed = backend.models_allowed(env.ref, env.settings)
        if args.get("reasoning_effort") not in allowed.get(args.get("model"), {}).get("reasoning_efforts", []):
            return f"{args.get('model')} at {args.get('reasoning_effort')} isn't allowed. Choose from the models allowed."
        if not str(args.get("instructions", "")).strip():
            return "A change needs the complete instructions."
    elif not str(args.get("recipe", "")).strip():
        return "A recipe needs its complete text."
    return None


def begin(env: AgentEnv, handler_id: str) -> int:
    """Open a round, working to the settings as they stand and following the current recipe."""
    return backend.start_round(env.engine, handler_id, backend.current_version(env.engine, "recipe")["version"], backend.get_settings(env.engine))


def work_round(env: AgentEnv, round_id: int, resume: dict | None = None) -> None:
    """One stretch of a round: its first evaluations and the improver's work, or the improver carrying on after a
    decision. It ends where the improver waits for a handler, or finishes. A round that has been stopped ends quietly."""
    try:
        if resume is None:
            if not first_evaluations(env, round_id):
                return
            backend.set_stage(env.engine, round_id, "improving")
            graph_input: Any = {"messages": [HumanMessage("Please carry out one round of your recipe.")]}
        else:
            backend.set_stage(env.engine, round_id, "improving")
            graph_input = Command(resume=resume)
        while True:
            result = _invoke(env, round_id, graph_input)
            if "__interrupt__" not in result:
                backend.finish_round(env.engine, round_id)  # a round that proposes nothing ends with no version tried
                return
            request = result["__interrupt__"][0].value["action_requests"][0]
            problem = _problem(env, request)
            if problem:  # back to the improver, and never to a handler
                graph_input = Command(resume={"decisions": [{"type": "reject", "message": problem}]})
                continue
            args = dict(request["args"])
            reason = str(args.pop("reason", ""))
            backend.add_proposal(env.engine, round_id, KINDS[request["name"]], args, reason)
            return
    except backend.RoundStopped:
        log.info("round %s was stopped", round_id)
    except Exception as exc:  # the round ends where it is, with what went wrong, and the service stays up
        log.exception("round %s failed", round_id)
        try:
            backend.finish_round(env.engine, round_id, error=f"{type(exc).__name__}: {exc}")
        except Exception:
            log.exception("could not record how round %s ended", round_id)


def record_decision(env: AgentEnv, round_id: int, proposal_id: int, approve: bool, reason: str, handler) -> dict:
    """A handler's decision on the proposal the round is waiting on: recorded, with who, before the improver carries on."""
    if backend.get_proposal(env.engine, proposal_id)["round_id"] != round_id:
        raise LookupError(f"proposal {proposal_id} is not in round {round_id}")
    return backend.decide_proposal(env.engine, proposal_id, approve, reason, handler)


def resume_round(env: AgentEnv, round_id: int, approve: bool, reason: str) -> None:
    """Carry the improver on from its pause, with the decision. A rejection's reason goes back to it."""
    decision = {"type": "approve"} if approve else {"type": "reject", "message": reason or "A handler rejected the proposal."}
    work_round(env, round_id, resume={"decisions": [decision]})
