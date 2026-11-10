"""What each tool answers, as plain data: `{"answer": ..., "sources": [...]}`. A source is a file with its system, owner and
review date. Something that cannot be found is a ValueError, which the server turns into a refusal."""
from context import files

SNIPPET_CHARS = 160
SEARCH_LIMIT = 5


def _sources(*names: str) -> list[dict]:
    return [files.SOURCES[name] for name in dict.fromkeys(names)]


def lookup_term(term: str) -> dict:
    wanted = term.strip().lower()
    for entry in files.TERMS:
        if entry["term"].lower() == wanted:
            return {"answer": {"term": entry["term"], "definition": " ".join(entry["definition"].split()), "sections": entry["sections"]},
                    "sources": _sources("glossary.yaml", "policy_wording.md")}
    return {"answer": {"unknown_term": term, "terms": [e["term"] for e in files.TERMS]}, "sources": _sources("glossary.yaml")}


def describe_fields(record: str) -> dict:
    if record not in files.FIELDS:
        raise ValueError(f"unknown record {record!r}. Records: {', '.join(files.FIELDS)}")
    return {"answer": files.FIELDS[record], "sources": _sources("data_dictionary.yaml")}


def get_plan(plan: str) -> dict:
    if plan not in files.PLANS:
        raise ValueError(f"unknown plan {plan!r}. Plans: {', '.join(files.PLANS)}")
    return {"answer": {"plan": files.PLANS[plan], "deductible_applies_to": files.DEDUCTIBLE_APPLIES_TO, "add_ons": files.ADD_ONS},
            "sources": _sources("plans.yaml")}


def get_claim_rules() -> dict:
    return {"answer": {"rules": files.RULES}, "sources": _sources("claim_rules.yaml")}


def search_reference_data(query: str) -> dict:
    words = query.lower().split()
    found = [{"topic": f["topic"], "fact": f["fact"]} for f in files.REFERENCE if words and all(w in f["fact"].lower() for w in words)]
    return {"answer": found, "sources": _sources("reference_data.yaml")}


def search_documents(query: str) -> dict:
    term = query.lower().strip()
    hits = []
    for section in files.SECTIONS if term else []:
        lowered = section.text.lower()
        count = lowered.count(term)
        if count:
            start = max(0, lowered.index(term) - SNIPPET_CHARS // 2)
            snippet = " ".join(section.text[start: start + SNIPPET_CHARS].split())
            hits.append((count, {"document": section.document, "section": section.number, "title": section.title,
                                 "snippet": snippet, "file": section.file}))
    hits.sort(key=lambda h: -h[0])
    found = [h for _, h in hits[:SEARCH_LIMIT]]
    return {"answer": found, "sources": _sources(*(h["file"] for h in found))}


def read_section(document: str, number: int) -> dict:
    if document not in files.DOCUMENTS:
        raise ValueError(f"unknown document {document!r}. Documents: {', '.join(files.DOCUMENTS)}")
    for section in files.SECTIONS:
        if section.document == document and section.number == number:
            return {"answer": {"document": document, "section": number, "title": section.title, "text": section.text},
                    "sources": _sources(section.file)}
    raise ValueError(f"{document} has no section {number}")
