"""Deterministic stand-ins for the model, used by the tests and the e2e runs (AGENT_PROVIDER=scripted).
They never call a model, and fail on purpose on a few claims so recovery can be exercised."""
import json
import sqlite3

from langchain_core.tools import BaseTool

from app.agent import CalcStep, Decision, Emit


# Over-limit approvals used by the scripted provider, so the approval flow can be exercised.
_SCRIPTED_OVER_LIMIT = {"CLM-1003": 3750.0, "CLM-1008": 2240.0, "CLM-1011": 1150.0}
# Failures used by the scripted provider, so recovery can be exercised end to end.
_SCRIPTED_ALWAYS_FAILS = {"CLM-1013"}
_SCRIPTED_FAILS_ONCE = {"CLM-1017"}


class ScriptedAgent:
    """Deterministic stand-in for tests and e2e runs. Never calls a model."""

    model_id = "scripted"
    usage = None

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def decide(self, claim_id: str, on_event: Emit) -> Decision:
        row = self.conn.execute("SELECT payload FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
        claim = json.loads(row["payload"])
        on_event("tool_call", {"name": "get_claim", "args": {}})
        on_event("tool_result", {"name": "get_claim", "content": claim["summary"]})
        on_event("tool_call", {"name": "read_section", "args": {"document": "policy_wording", "section": 10}})
        on_event("tool_result", {"name": "read_section",
                                 "content": "[policy_wording section 10: How we work out your payment, 600 characters]"})
        attempts = self.conn.execute("SELECT attempts FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()[0]
        if claim_id in _SCRIPTED_ALWAYS_FAILS or (claim_id in _SCRIPTED_FAILS_ONCE and attempts == 0):
            raise RuntimeError("scripted failure")
        if claim_id in _SCRIPTED_OVER_LIMIT:
            decision = self._approve(_SCRIPTED_OVER_LIMIT[claim_id])
        elif claim_id.endswith("5"):
            decision = Decision(
                decision="decline",
                rationale="The claim is excluded. Section 9 of the policy wording does not pay it.",
                citations=["Policy wording, section 9"],
            )
        elif claim_id.endswith("9"):
            decision = Decision(
                decision="refer",
                rationale="A document is missing. Section 11 of the policy wording says the claim waits.",
                citations=["Policy wording, section 11"],
            )
        else:
            decision = self._approve(round(min(claim["amount_claimed"] * 0.5, 900.0), 2))
        on_event("decision", decision.model_dump())
        return decision

    @staticmethod
    def _approve(payout: float) -> Decision:
        return Decision(
            decision="approve",
            payout=payout,
            calculation=[CalcStep(step="Eligible amount", amount=payout)],
            rationale="The loss is covered. Section 10 of the policy wording sets the payment.",
            citations=["Policy wording, section 10"],
        )


class ScriptedOrchestrator:
    """Deterministic stand-in for tests and e2e runs: works claims in id order. Never calls a model."""

    model_id = "scripted"
    usage = None

    def run(self, tools: list[BaseTool], on_event: Emit) -> None:
        by_name = {t.name: t for t in tools}

        def call(name: str, args: dict) -> str:
            on_event("tool_call", {"name": name, "args": args})
            result = by_name[name].invoke(args)
            on_event("tool_result", {"name": name, "content": result})
            return result

        listed = json.loads(call("list_waiting_claims", {}))
        room = None if listed["cap"] is None else listed["cap"] - listed["taken"]
        plan = [c["claim_id"] for c in listed["claims"]][:room]
        call("set_todo", {"items": [{"title": f"Work {cid}", "claim_id": cid} for cid in plan]})
        reports = [call("work_claim", {"claim_id": cid}) for cid in plan]
        for _ in range(3):  # claims that came back to the queue are worked again after the others
            answer = call("finish_run", {"summary": self._summary(reports)})
            if not answer.startswith("Not finished"):
                return
            owed = answer.split(": ", 1)[1].split(", ")
            reports += [call("work_claim", {"claim_id": cid}) for cid in owed]

    @staticmethod
    def _summary(reports: list[str]) -> str:
        latest = {line.split(":", 1)[0]: line for line in reports}
        return f"Worked {len(latest)} claims.\n" + "\n".join(latest.values())
