"""The policy wording and the claims manual, kept in the documents table and cut into their numbered sections."""
import json

import pytest

from app.documents import Documents
from app.reference import Reference
from app.seed import seed_claims
from tests.fakes import connect

CONTEXT = None  # seeding reads backend/context by default


@pytest.fixture
def docs(db_path, data_dir) -> Documents:
    conn = connect(db_path)
    from app.db import init_schema

    init_schema(conn.engine)
    seed_claims(conn.engine, Reference(data_dir))
    return Documents(conn.engine)


def test_wording_has_twelve_sections(docs):
    sections = docs.sections("policy_wording")
    assert [s.number for s in sections] == list(range(1, 13))
    assert [s.title for s in sections[:3]] == ["Your plan", "Trip Cancellation and Interruption", "Trip Delay"]


def test_manual_has_fourteen_sections(docs):
    sections = docs.sections("claims_manual")
    assert len(sections) == 14
    assert docs.section("claims_manual", 10).title == "Meals without receipts"


def test_section_text_stops_at_next_heading_and_has_no_front_matter(docs):
    text = docs.section("claims_manual", 9).text
    assert "third claim" in text
    assert "## 10." not in text
    assert not any(s.text.startswith("---") or "reviewed:" in s.text for d in ("policy_wording", "claims_manual") for s in docs.sections(d))


@pytest.mark.parametrize("document,number", [
    ("claims_manual", 99),
    ("evals", 1),
    ("../evals/decisions.json", 1),
])
def test_unknown_document_or_section_raises_key_error(docs, document, number):
    with pytest.raises(KeyError):
        docs.section(document, number)


def test_versions_come_from_the_front_matter(docs):
    assert docs.version("policy_wording") == "2026.1"
    assert docs.version("claims_manual") == "2026.2"


def test_each_documents_source_is_kept(docs):
    assert docs.source("claims_manual") == {"file": "claims_manual.md", "system": "Claims operations",
                                            "owner": "Head of claims operations", "reviewed": "2026-09-01"}
    assert docs.source("policy_wording")["owner"] == "Legal"


def test_seeding_twice_changes_nothing(db_path, data_dir):
    from app.db import documents, init_schema
    from sqlalchemy import func, select

    conn = connect(db_path)
    init_schema(conn.engine)
    for _ in range(2):
        seed_claims(conn.engine, Reference(data_dir))
    with conn.engine.connect() as c:
        assert c.execute(select(func.count()).select_from(documents)).scalar_one() == 2


def test_the_app_never_reads_plans_or_the_documents_from_data(tmp_path, data_dir):
    """The app's data folder may lack plans.json and the two documents: the product reads the context layer's files only
    while seeding, and nothing else reads them."""
    import shutil

    from app.api import create_app
    from app.config import Settings
    from tests.fakes import MINT, TEST_KEY, api_client

    data = tmp_path / "data"
    shutil.copytree(data_dir, data)
    for name in ("plans.json", "policy_wording.md", "claims_manual.md", "knowledge.json"):
        (data / name).unlink()
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=str(tmp_path / "t.db"), data_dir=str(data))
    with api_client(create_app(settings, jwks=MINT)) as client:
        key = {"X-API-Key": TEST_KEY}
        assert client.get("/claims/CLM-1001", headers=key).status_code == 200
        assert client.get("/agent/documents/claims_manual", headers=key).json()[0]["title"] == "Purpose and scope"
        client.post("/agent/claims/CLM-1001/take", json={"run_id": None}, headers=key)
        body = {"decision": "decline", "payout": 0, "rationale": "x. Section 9.", "citations": ["x"], "decided_by": "Test", "model": "m"}
        done = client.post("/claims/CLM-1001/decision", json=body, headers=key)
        assert done.status_code == 200
        assert (done.json()["decision"]["manual_version"], done.json()["decision"]["wording_version"]) == ("2026.2", "2026.1")
        assert json.loads(json.dumps(done.json()))["decision"]["decision"] == "decline"
