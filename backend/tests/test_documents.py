import pytest

from app.documents import Documents
from app.reference import Reference


@pytest.fixture
def docs(data_dir) -> Documents:
    return Documents(Reference(data_dir))


def test_wording_has_twelve_sections(docs):
    sections = docs.sections("policy_wording")
    assert [s.number for s in sections] == list(range(1, 13))
    assert [s.title for s in sections[:3]] == ["Your plan", "Trip Cancellation and Interruption", "Trip Delay"]


def test_manual_has_fourteen_sections(docs):
    sections = docs.sections("claims_manual")
    assert len(sections) == 14
    assert docs.section("claims_manual", 10).title == "Meals without receipts"


def test_section_text_stops_at_next_heading(docs):
    text = docs.section("claims_manual", 9).text
    assert "third claim" in text
    assert "## 10." not in text


@pytest.mark.parametrize("document,number", [
    ("claims_manual", 99),
    ("evals", 1),
    ("../evals/decisions.json", 1),
])
def test_unknown_document_or_section_raises_key_error(docs, document, number):
    with pytest.raises(KeyError):
        docs.section(document, number)


def test_search_finds_wild_animals_in_manual_section_7(docs):
    hit = docs.search("wild animal")[0]
    assert (hit["document"], hit["section"]) == ("claims_manual", 7)
    assert "wild animal" in hit["snippet"].lower()


def test_search_with_no_match_returns_empty_list(docs):
    assert docs.search("zeppelin hijacking") == []


def test_versions(docs):
    assert docs.version("policy_wording") == "2026.1"
    assert docs.version("claims_manual") == "2026.2"
