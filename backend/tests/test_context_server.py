"""The context layer's tools, against the real files in context/."""
import asyncio
import builtins
import inspect
from pathlib import Path

import pytest

from context import files, tools
from context.server import build_server

TOOL_NAMES = {"lookup_term", "describe_fields", "get_plan", "get_claim_rules", "search_reference_data", "search_documents", "read_section"}


def test_lookup_term_matches_any_case_and_names_its_file():
    answer = tools.lookup_term("CARRIER")
    assert answer["answer"]["term"] == "Carrier" and answer["answer"]["sections"] == [12]
    glossary = answer["sources"][0]
    assert (glossary["file"], glossary["system"], glossary["owner"]) == ("glossary.yaml", "The policy wording, version 2026.1", "Legal")


def test_an_unknown_term_gets_the_glossarys_terms():
    answer = tools.lookup_term("zebra")["answer"]
    assert answer["unknown_term"] == "zebra" and "Carrier" in answer["terms"] and "Deductible" in answer["terms"]


@pytest.mark.parametrize("record", ["claim", "policy", "previous_claim"])
def test_describe_fields_for_each_record(record):
    answer = tools.describe_fields(record)
    assert answer["answer"]["record"] == record and answer["answer"]["fields"]
    assert answer["sources"][0]["file"] == "data_dictionary.yaml"


def test_an_unknown_record_is_refused():
    with pytest.raises(ValueError, match="unknown record 'customer'"):
        tools.describe_fields("customer")


def test_get_plan_has_limits_deductible_what_it_applies_to_and_add_ons():
    answer = tools.get_plan("premium")["answer"]
    assert answer["plan"]["deductible"] == 50.0 and answer["plan"]["trip_delay_waiting_hours"] == 6
    assert answer["deductible_applies_to"] == ["baggage", "emergency_medical"]
    assert {a["add_on"] for a in answer["add_ons"]} == {"adventure_sports", "pre_existing_waiver", "electronics_plus"}
    with pytest.raises(ValueError, match="unknown plan"):
        tools.get_plan("gold")


def test_get_claim_rules():
    rules = {r["rule"]: r for r in tools.get_claim_rules()["answer"]["rules"]}
    assert rules["filing_window"]["days"] == 90 and rules["meals_without_receipts"]["amount"] == 35.0


def test_search_reference_data_needs_every_word_in_any_case():
    found = tools.search_reference_data("lisbon STRIKE")["answer"]
    assert found and all(f["topic"] == "events" and "Lisbon" in f["fact"] for f in found)
    assert tools.search_reference_data("lisbon hurricane")["answer"] == []
    assert tools.search_reference_data("   ")["answer"] == []


def test_search_documents_names_each_hits_file():
    answer = tools.search_documents("deductible")
    assert answer["answer"] and {h["file"] for h in answer["answer"]} <= {"policy_wording.md", "claims_manual.md"}
    assert {s["file"] for s in answer["sources"]} == {h["file"] for h in answer["answer"]}
    assert all(h["document"] in files.DOCUMENTS and isinstance(h["section"], int) for h in answer["answer"])
    assert tools.search_documents("")["answer"] == []


def test_read_section_has_no_front_matter_and_its_documents_source():
    answer = tools.read_section("claims_manual", 14)
    assert answer["answer"]["title"] == "Records" and answer["answer"]["text"].startswith("## 14. Records")
    assert "source:" not in answer["answer"]["text"] and "reviewed" not in answer["answer"]["text"]
    assert answer["sources"][0]["file"] == "claims_manual.md" and answer["sources"][0]["owner"] == "Head of claims operations"


def test_a_section_that_does_not_exist_is_refused():
    with pytest.raises(ValueError, match="claims_manual has no section 99"):
        tools.read_section("claims_manual", 99)
    with pytest.raises(ValueError, match="unknown document"):
        tools.read_section("brochure", 1)


def test_every_document_is_split_at_its_numbered_headings_with_its_version():
    manual = [s for s in files.SECTIONS if s.document == "claims_manual"]
    assert [s.number for s in manual] == list(range(1, len(manual) + 1))
    assert files.VERSIONS == {"policy_wording": "2026.1", "claims_manual": "2026.2"}
    assert not any(s.text.startswith("---") for s in files.SECTIONS)


def server_tools():
    loop = asyncio.new_event_loop()  # not asyncio.run: it leaves the main thread with no loop, which Mangum's tests need
    try:
        return loop.run_until_complete(build_server().list_tools())
    finally:
        loop.close()


def test_every_tool_has_a_one_line_description_no_optional_inputs_and_is_read_only():
    listed = server_tools()
    assert {t.name for t in listed} == TOOL_NAMES
    for tool in listed:
        assert tool.description and "\n" not in tool.description.strip(), tool.name
        schema = tool.inputSchema
        assert set(schema.get("required", [])) == set(schema.get("properties", {})), tool.name
        assert tool.annotations and tool.annotations.readOnlyHint is True, tool.name


def test_the_files_are_read_once_when_the_process_starts_never_for_a_request(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a file was read for a request")

    monkeypatch.setattr(Path, "read_text", refuse)
    monkeypatch.setattr(builtins, "open", refuse)
    tools.lookup_term("carrier")
    tools.describe_fields("claim")
    tools.get_plan("first")
    tools.get_claim_rules()
    tools.search_reference_data("storm")
    tools.search_documents("claim")
    tools.read_section("policy_wording", 10)


def test_the_context_package_does_not_import_the_app_or_the_agent():
    for module in Path(inspect.getfile(files)).parent.glob("*.py"):
        source = module.read_text(encoding="utf-8")
        assert "import app" not in source and "from app" not in source and "from agent" not in source, module.name
