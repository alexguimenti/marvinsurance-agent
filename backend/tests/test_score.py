import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from score import score, select_tiny  # noqa: E402


def expected(cid, decision, payout, tiny=False, over=False):
    return {"claim_id": cid, "decision": decision, "payout": payout, "tiny": tiny,
            "over_approval_limit": over, "difficulty": "routine", "needs": []}


def test_score_counts_decision_and_payout_separately():
    exp = [expected("A", "approve", 100.0), expected("B", "decline", 0.0)]
    got = [{"claim_id": "A", "decision": "approve", "payout": 90.0},
           {"claim_id": "B", "decision": "decline", "payout": 0.0}]
    result = score(got, exp)
    assert result["decision_accuracy"] == 1.0
    assert result["payout_accuracy"] == 0.5
    assert len(result["mismatches"]) == 1 and result["mismatches"][0].startswith("A:")


def test_over_limit_approve_matches_approve():
    exp = [expected("A", "approve", 3750.0, over=True)]
    got = [{"claim_id": "A", "decision": "approve", "payout": 3750.0}]
    result = score(got, exp)
    assert result["decision_accuracy"] == 1.0 and result["payout_accuracy"] == 1.0
    assert result["mismatches"] == []


def test_payout_within_a_cent_matches():
    exp = [expected("A", "approve", 100.0)]
    got = [{"claim_id": "A", "decision": "approve", "payout": 100.004}]
    assert score(got, exp)["payout_accuracy"] == 1.0


def test_missing_decision_counts_as_wrong():
    result = score([], [expected("A", "approve", 10.0)])
    assert result["decision_accuracy"] == 0.0 and result["mismatches"]


def test_tiny_filter_keeps_flagged_claims():
    exp = [expected("A", "approve", 1.0, tiny=True), expected("B", "approve", 1.0)]
    assert [e["claim_id"] for e in select_tiny(exp)] == ["A"]


def test_run_agent_decides_each_claim_through_work_claim(data_dir):
    import json

    from app.config import Settings
    from score import _run_agent

    claims = json.loads((ROOT / "data" / "claims.json").read_text(encoding="utf-8"))
    picked = [c for c in claims if c["claim_id"] in ("CLM-1001", "CLM-1017")]  # CLM-1017 fails once when scripted
    settings = Settings(_env_file=None, agent_provider="scripted", data_dir=data_dir)
    decisions = _run_agent(picked, settings)
    assert sorted(d["claim_id"] for d in decisions) == ["CLM-1001", "CLM-1017"]


def test_a_test_cannot_reach_a_real_model():
    import pytest
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(model="x", api_key="not-a-key", base_url="http://127.0.0.1:9", max_retries=0)
    with pytest.raises(AssertionError, match="real model"):
        model.invoke("hello")
