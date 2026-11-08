"""The improvement loop's records and its rule: rounds, the improver's proposals and who decided them, a version's
scores, what the improver is allowed to see, the spending estimate, and the rule that keeps or rolls back a version.
Code, not the improver, decides. Nothing here is reachable by the claim agent."""
import json
import secrets
from datetime import datetime, timezone
from statistics import mean

from sqlalchemy import Engine, and_, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app import configs, scoring
from app.auth import Handler
from app.config import Settings
from app.db import judge_grades, model_calls, plain, proposals, rounds, runs
from app.reference import Reference
from app.settings_store import models_allowed

RULE = ("A new version is kept when it meets the objectives, at least one score improves by more than the noise "
        "and none gets worse by more than the tolerance")
SCORES = ("accuracy", "grade", "decision_accuracy", "cost_per_claim", "seconds_per_claim", "mistake_cost", "straight_through")
FIRST_EVALUATIONS = {"evaluation": 1, "held_out": 2}  # what a version needs before a round can compare against it
FALLBACK_COST_PER_CLAIM = 0.01  # dollars, when no evaluation has run yet: a careful guess, not a cheap one
ENDS = ("finished", "stopped")


class RoundOpen(Exception):
    """Another round is still open."""


class RoundStopped(Exception):
    """The round has ended, so nothing more is recorded on it."""


class AlreadyDecided(Exception):
    """A handler already approved or rejected this proposal."""


# --- the rule ---------------------------------------------------------------------------------------------

def compare(new: dict, old: dict, noise: dict, tolerance: dict) -> tuple[list[str], list[str]]:
    """Which scores got better by more than the noise, and which got worse by more than the tolerance. Accuracy and the
    grade compare as differences; cost and time as fractions of the old value."""
    better, worse = [], []
    for name in ("accuracy", "grade"):
        if new.get(name) is None or old.get(name) is None:
            continue
        if new[name] > old[name] + noise[name]:
            better.append(name)
        if new[name] < old[name] - tolerance[name]:
            worse.append(name)
    for name in ("cost_per_claim", "seconds_per_claim"):
        if new.get(name) is None or old.get(name) is None:
            continue
        if new[name] < old[name] * (1 - noise[name]):
            better.append(name)
        if new[name] > old[name] * (1 + tolerance[name]):
            worse.append(name)
    return better, worse


def keep_or_roll_back(new: dict, old: dict, settings: dict) -> tuple[bool, str]:
    """Keep a new version, from its held-out scores and the current version's, or roll it back, and say why."""
    if new.get("grade") is None:
        return False, "it has no grade"
    missed = [name for name, floor in settings["objectives"].items() if new.get(name) is None or new[name] < floor]
    better, worse = compare(new, old, settings["noise"], settings["tolerance"])
    if missed:
        return False, f"it missed the objectives on {', '.join(missed)}"
    if worse:
        return False, f"{', '.join(worse)} got worse"
    if not better:
        return False, "no score improved by more than the noise"
    return True, f"{', '.join(better)} improved"


# --- a version's scores -----------------------------------------------------------------------------------

def _round_evaluations(engine: Engine, version: int, eval_set: str) -> list[int]:
    """The finished whole-set evaluations that rounds ran on a version: not the Scoreboard's, not a tiny set's."""
    query = select(runs.c.run_id).where(
        runs.c.kind == "evaluation", runs.c.config_version == version, runs.c.round_id.is_not(None),
        runs.c.tiny.is_(False), runs.c.status == "finished", runs.c.eval_set == eval_set).order_by(runs.c.run_id)
    with engine.connect() as conn:
        return list(conn.execute(query).scalars())


def version_scores(engine: Engine, version: int) -> dict:
    """A version's scores per set: the averages of its rounds' whole-set evaluations, and how many there were."""
    out = {}
    for key, eval_set in (("evaluation", "evaluation"), ("held-out", "held_out")):
        ids = _round_evaluations(engine, version, eval_set)
        if not ids:
            continue
        measured = [scoring.evaluation_report(engine, i)["measures"] for i in ids]
        averages = {}
        for name in SCORES:
            values = [m[name] for m in measured if m.get(name) is not None]
            averages[name] = mean(values) if values else None
        out[key] = {**averages, "runs": len(ids)}
    return out


def needs_first_evaluations(engine: Engine, version: int) -> list[str]:
    """The evaluations a version still owes before a round can use its scores: one on the evaluation set, two held-out."""
    owed = []
    for eval_set, wanted in FIRST_EVALUATIONS.items():
        owed += [eval_set] * max(0, wanted - len(_round_evaluations(engine, version, eval_set)))
    return owed


# --- what the improver sees -------------------------------------------------------------------------------

def _latest_evaluation_set_run(engine: Engine, version: int) -> int | None:
    query = select(runs.c.run_id).where(
        runs.c.kind == "evaluation", runs.c.config_version == version, runs.c.eval_set == "evaluation",
        runs.c.tiny.is_(False), runs.c.status == "finished").order_by(runs.c.run_id.desc()).limit(1)
    with engine.connect() as conn:
        return conn.execute(query).scalar_one_or_none()


def _earlier_rounds(engine: Engine, before_round: int) -> list[dict]:
    with engine.connect() as conn:
        found = conn.execute(select(rounds).where(rounds.c.round_id < before_round).order_by(rounds.c.round_id)).mappings().all()
    return [{"round": r["round_id"], "recipe_version": r["recipe_version"], "version_tried": r["version_tried"],
             "reason": r["reason"], "verdict": r["verdict"], "why": r["why"],
             "held_out_before": json.loads(r["before"]) if r["before"] else None,
             "held_out_after": json.loads(r["after"]) if r["after"] else None} for r in found]


def improver_view(engine: Engine, ref: Reference, settings: Settings, round_id: int) -> dict:
    """What the improver reads. From the evaluation set: the claims the current version got wrong and the five right
    ones it explained least well, with the handlers' decisions and reasons. From the held-out set: scores, and nothing
    else. It works to the settings as they stood when the round started."""
    round_row = get_round(engine, round_id)
    round_settings = json.loads(round_row["settings"])
    current = configs.current(engine, "config")
    run_id = _latest_evaluation_set_run(engine, current["version"])
    review = scoring.evaluation_set_review(engine, run_id) if run_id else []
    wrong = [c for c in review if not c["accurate"]]
    right = sorted((c for c in review if c["accurate"]), key=lambda c: (c["judge"]["score"] is None, c["judge"]["score"] or 0))
    rows = lambda items: [{k: v for k, v in c.items() if k != "accurate"} for c in items]  # noqa: E731
    return {
        "current_version": {k: current[k] for k in ("version", "instructions", "model", "reasoning_effort")},
        "scores": version_scores(engine, current["version"]),
        "evaluation_set": {"wrong": rows(wrong), "explained_least_well": rows(right[:5])},
        "earlier_rounds": _earlier_rounds(engine, round_id),
        "objectives_on_the_held_out_set": round_settings["objectives"],
        "noise": round_settings["noise"],
        "tolerance": round_settings["tolerance"],
        "rule": RULE,
        "spending_left_usd": round(round_settings["round_spending_limit"] - round_spent(engine, round_id), 4),
        "models_allowed": models_allowed(ref, settings),
    }


# --- the spending -----------------------------------------------------------------------------------------

def _sum(engine: Engine, query) -> float:
    with engine.connect() as conn:
        return float(conn.execute(query).scalar_one() or 0.0)


def round_spent(engine: Engine, round_id: int) -> float:
    """What a round has cost: the improver's own model calls, every one of its evaluations' claim agents, and the judge."""
    its_runs = select(runs.c.run_id).where(runs.c.round_id == round_id)
    improver = _sum(engine, select(func.sum(model_calls.c.cost_usd)).where(model_calls.c.round_id == round_id))
    agents = _sum(engine, select(func.sum(model_calls.c.cost_usd)).where(model_calls.c.run_id.in_(its_runs)))
    judged = _sum(engine, select(func.sum(judge_grades.c.cost_usd)).where(judge_grades.c.run_id.in_(its_runs)))
    return improver + agents + judged


def _highest_cost_per_claim(engine: Engine, model: str) -> float | None:
    """The most a single claim has cost in an evaluation on this model: its claim agent's calls and the judge."""
    totals: dict[tuple[int, str], float] = {}
    of_the_model = select(runs.c.run_id).where(runs.c.model == model, runs.c.kind == "evaluation")
    with engine.connect() as conn:
        for run_id, claim_id, cost in conn.execute(
            select(model_calls.c.run_id, model_calls.c.claim_id, func.sum(model_calls.c.cost_usd))
            .where(model_calls.c.run_id.in_(of_the_model)).group_by(model_calls.c.run_id, model_calls.c.claim_id)):
            totals[(run_id, claim_id)] = totals.get((run_id, claim_id), 0.0) + float(cost or 0.0)
        for run_id, claim_id, cost in conn.execute(
            select(judge_grades.c.run_id, judge_grades.c.claim_id, judge_grades.c.cost_usd).where(judge_grades.c.run_id.in_(of_the_model))):
            totals[(run_id, claim_id)] = totals.get((run_id, claim_id), 0.0) + float(cost or 0.0)
    return max(totals.values()) if totals else None


def estimate(engine: Engine, model: str, claims: int, models: dict, current_model: str) -> float:
    """What an evaluation is likely to cost: its claims times the highest cost per claim seen for its model. For a model
    with no runs yet, the current model's, scaled by the larger of the ratios of their input and output prices."""
    per_claim = _highest_cost_per_claim(engine, model)
    if per_claim is None:
        base = _highest_cost_per_claim(engine, current_model)
        base = FALLBACK_COST_PER_CLAIM if base is None else base
        ratios = [models[model][key] / models[current_model][key]
                  for key in ("price_in", "price_out") if model in models and current_model in models and models[current_model][key]]
        per_claim = base * (max(ratios) if ratios else 1.0)
    return claims * per_claim


# --- rounds and proposals ---------------------------------------------------------------------------------

def get_round(engine: Engine, round_id: int):
    with engine.connect() as conn:
        row = conn.execute(select(rounds).where(rounds.c.round_id == round_id)).mappings().first()
    if row is None:
        raise LookupError(f"there is no round {round_id}")
    return row


def open_round(engine: Engine):
    with engine.connect() as conn:
        return conn.execute(select(rounds).where(rounds.c.open.is_(True))).mappings().first()


def list_rounds(engine: Engine) -> list:
    with engine.connect() as conn:
        return list(conn.execute(select(rounds).order_by(rounds.c.round_id.desc())).mappings())


def start_round(engine: Engine, started_by: str, recipe_version: int, settings: dict) -> int:
    """Open a round, working to these settings as they stand now. Raises RoundOpen while another round is open."""
    try:
        with engine.begin() as conn:
            round_id = conn.execute(insert(rounds).values(
                stage="first_evaluations", open=True, recipe_version=recipe_version, settings=json.dumps(settings),
                thread_id="pending", started_by=started_by).returning(rounds.c.round_id)).scalar_one()
    except IntegrityError as exc:  # the one_open_round index
        raise RoundOpen("a round is already open") from exc
    with engine.begin() as conn:  # a new thread for each round, so a reseed never meets an old round's checkpoints
        conn.execute(update(rounds).where(rounds.c.round_id == round_id).values(thread_id=f"round-{round_id}-{secrets.token_hex(4)}"))
    return round_id


def _open(round_id: int):
    return and_(rounds.c.round_id == round_id, rounds.c.open.is_(True))


def set_stage(engine: Engine, round_id: int, stage: str, progress: str | None = None) -> None:
    """Say where the round has got to, as it happens. A round that has ended refuses it."""
    with engine.begin() as conn:
        done = conn.execute(update(rounds).where(_open(round_id)).values(stage=stage, progress=progress)).rowcount
    if not done:
        raise RoundStopped(f"round {round_id} has ended")


def add_proposal(engine: Engine, round_id: int, kind: str, payload: dict, reason: str) -> int:
    """Record what the improver proposes and put the round to waiting for a handler. Nothing takes effect yet."""
    stage = "awaiting_change" if kind == "change" else "awaiting_recipe"
    with engine.begin() as conn:
        if not conn.execute(update(rounds).where(_open(round_id)).values(stage=stage, progress=None)).rowcount:
            raise RoundStopped(f"round {round_id} has ended")
        return conn.execute(insert(proposals).values(
            round_id=round_id, kind=kind, payload=json.dumps(payload), reason=reason, status="waiting",
        ).returning(proposals.c.id)).scalar_one()


def waiting_proposal(engine: Engine, round_id: int):
    with engine.connect() as conn:
        return conn.execute(select(proposals).where(proposals.c.round_id == round_id, proposals.c.status == "waiting")
                            .order_by(proposals.c.id.desc())).mappings().first()


def decide_proposal(engine: Engine, proposal_id: int, approve: bool, reason: str, handler: Handler) -> dict:
    """A handler approves or rejects a proposal. One decision only; a second, or one on a round that has ended, is refused
    and records nothing. An approved change becomes a version, not yet the current one; an approved recipe becomes the
    current recipe."""
    still_open = select(rounds.c.round_id).where(rounds.c.open.is_(True))
    with engine.begin() as conn:
        row = conn.execute(
            update(proposals).where(proposals.c.id == proposal_id, proposals.c.status == "waiting", proposals.c.round_id.in_(still_open))
            .values(status="approved" if approve else "rejected", decided_by=handler.handler_id,
                    decided_at=datetime.now(timezone.utc), decision_reason=reason or None).returning(proposals)
        ).mappings().first()
    if row is None:
        with engine.connect() as conn:
            found = conn.execute(select(proposals.c.status, proposals.c.round_id).where(proposals.c.id == proposal_id)).first()
        if found is None:
            raise LookupError(f"there is no proposal {proposal_id}")
        if found[0] == "waiting":
            raise RoundStopped(f"round {found[1]} has ended")
        raise AlreadyDecided(f"proposal {proposal_id} was already {found[0]}")
    out = plain(row)
    out["payload"] = json.loads(row["payload"])
    if not approve:
        return out
    payload = out["payload"]
    if row["kind"] == "change":
        version = configs.add_version(engine, "config", {**payload, "reason": row["reason"], "proposed_by": "improver"},
                                      approved_by=handler.handler_id, round_id=row["round_id"])
        with engine.begin() as conn:
            conn.execute(update(rounds).where(rounds.c.round_id == row["round_id"]).values(version_tried=version, reason=row["reason"]))
    else:
        version = configs.add_version(engine, "recipe", {**payload, "reason": row["reason"], "proposed_by": "improver"},
                                      approved_by=handler.handler_id, round_id=row["round_id"])
        configs.make_current(engine, "recipe", version)
    out["version"] = version
    return out


def record_verdict(engine: Engine, round_id: int, kept: bool, why: str, before: dict, after: dict) -> None:
    """Record whether the version was kept, and make a kept one current. A round that has ended refuses it."""
    with engine.begin() as conn:
        done = conn.execute(update(rounds).where(_open(round_id)).values(
            stage="verdict", progress=None, verdict="kept" if kept else "rolled_back", why=why,
            before=json.dumps(before), after=json.dumps(after))).rowcount
    if not done:
        raise RoundStopped(f"round {round_id} has ended")
    if kept:
        configs.make_current(engine, "config", get_round(engine, round_id)["version_tried"])


def finish_round(engine: Engine, round_id: int, error: str | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(update(rounds).where(_open(round_id)).values(
            stage="finished", open=False, progress=None, error=error, finished_at=datetime.now(timezone.utc)))


def stop_round(engine: Engine, round_id: int, handler_id: str | None) -> bool:
    """End a round that is open. What its verdict already decided stays. False if it had ended already."""
    with engine.begin() as conn:
        return bool(conn.execute(update(rounds).where(_open(round_id)).values(
            stage="stopped", open=False, progress=None, stopped_by=handler_id, finished_at=datetime.now(timezone.utc))).rowcount)


def stop_working_rounds(engine: Engine) -> list[int]:
    """The service has just started: a round that was in the middle of its work lost its process, and ends stopped. One
    waiting for a handler is still waiting."""
    working = ("first_evaluations", "improving", "evaluating", "verdict")
    with engine.begin() as conn:
        return list(conn.execute(update(rounds).where(rounds.c.open.is_(True), rounds.c.stage.in_(working)).values(
            stage="stopped", open=False, progress=None, finished_at=datetime.now(timezone.utc)).returning(rounds.c.round_id)).scalars())
