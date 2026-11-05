import pytest

from app.reference import ALLOWED_FILES, Reference


@pytest.mark.parametrize(
    "name",
    [
        "evals/decisions.json",
        "security/claims.json",
        "../data/evals/decisions.json",
        "policy_wording_short.md",
    ],
)
def test_reference_rejects_files_outside_whitelist(data_dir, name):
    with pytest.raises(ValueError):
        Reference(data_dir).read(name)


def test_whitelist_excludes_evals_and_security():
    assert not any("evals" in f or "security" in f for f in ALLOWED_FILES)


def test_manual_is_allowed(data_dir):
    assert Reference(data_dir).manual.startswith("# MarvInsurance Claims Handling Manual")


def test_reference_lookups(data_dir):
    ref = Reference(data_dir)
    assert ref.policy("POL-2001")["plan"] == "premium"
    assert ref.customer("CUS-3001")["name"] == "Maya Patel"
    assert ref.plan("premium")["deductible"] == 50.0
    assert ref.company["payout_approval_limit"] == 1000.0
    assert {h["handler_id"] for h in ref.handlers} >= {"HND-01"}
    assert "## 10. How we work out your payment" in ref.read("policy_wording.md")
    assert len(ref.knowledge) == 33
    assert {a["add_on"] for a in ref.add_ons} == {
        "adventure_sports", "pre_existing_waiver", "electronics_plus"
    }


def test_unknown_policy_or_customer_is_a_clear_lookup_error(data_dir):
    ref = Reference(data_dir)
    with pytest.raises(LookupError, match="POL-0000"):
        ref.policy("POL-0000")
    with pytest.raises(LookupError, match="CUS-0000"):
        ref.customer("CUS-0000")
