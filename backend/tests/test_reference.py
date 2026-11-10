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


@pytest.mark.parametrize("name", ["plans.json", "policy_wording.md", "claims_manual.md", "knowledge.json"])
def test_the_product_no_longer_reads_what_the_context_layer_serves(data_dir, name):
    assert name not in ALLOWED_FILES
    with pytest.raises(ValueError):
        Reference(data_dir).read(name)


def test_reference_lookups(data_dir):
    ref = Reference(data_dir)
    assert ref.policy("POL-2001")["plan"] == "premium"
    assert ref.customer("CUS-3001")["name"] == "Maya Patel"
    assert ref.company["payout_approval_limit"] == 1000.0
    assert {h["handler_id"] for h in ref.handlers} >= {"HND-01"}


def test_unknown_policy_or_customer_is_a_clear_lookup_error(data_dir):
    ref = Reference(data_dir)
    with pytest.raises(LookupError, match="POL-0000"):
        ref.policy("POL-0000")
    with pytest.raises(LookupError, match="CUS-0000"):
        ref.customer("CUS-0000")


def test_the_security_claim_joins_the_queues_files_and_its_decision_is_never_read(data_dir):
    ref = Reference(data_dir)
    assert [c["claim_id"] for c in ref.claims][-1] == "CLM-1901" and len(ref.claims) == 26
    claim = next(c for c in ref.claims if c["claim_id"] == "CLM-1901")
    assert ref.policy(claim["policy_id"])["policy_id"] == claim["policy_id"]
    assert ref.customer(claim["customer_id"])["customer_id"] == claim["customer_id"]
    with pytest.raises(ValueError):
        ref.read("security/decisions.json")  # the handler's decision on it is not a reference file
