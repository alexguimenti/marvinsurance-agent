"""The seven files, read once when the process starts and never again, and the documents cut into sections.

Each file names the system it comes from, its owner and when it was last reviewed. A document's front matter is its source
and its version, never part of a section."""
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

FOLDER = Path(__file__).resolve().parent
DOCUMENTS = {"policy_wording": "policy_wording.md", "claims_manual": "claims_manual.md"}
_HEADING = re.compile(r"^## (\d+)\. (.+)$", re.MULTILINE)
_FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


@dataclass(frozen=True)
class Section:
    document: str
    number: int
    title: str
    text: str
    file: str


def split_front_matter(text: str) -> tuple[dict, str]:
    """A document's front matter (its source and version) and what follows it."""
    found = _FRONT_MATTER.match(text)
    if not found:
        return {}, text
    return yaml.safe_load(found[1]), text[found.end():]


def _source(file: str, source: dict) -> dict:
    return {"file": file, "system": source["system"], "owner": source["owner"], "reviewed": str(source["reviewed"])}


def _yaml(file: str) -> dict:
    return yaml.safe_load((FOLDER / file).read_text(encoding="utf-8"))


def _sections(document: str, file: str, body: str) -> list[Section]:
    heads = list(_HEADING.finditer(body))
    return [
        Section(document, int(h[1]), h[2].strip(), body[h.start(): heads[i + 1].start() if i + 1 < len(heads) else len(body)].strip(), file)
        for i, h in enumerate(heads)
    ]


SOURCES: dict[str, dict] = {}
VERSIONS: dict[str, str] = {}
SECTIONS: list[Section] = []

_loaded = {name: _yaml(name) for name in ("data_dictionary.yaml", "glossary.yaml", "plans.yaml", "claim_rules.yaml", "reference_data.yaml")}
for _file, _data in _loaded.items():
    SOURCES[_file] = _source(_file, _data["source"])
for _document, _file in DOCUMENTS.items():
    _front, _body = split_front_matter((FOLDER / _file).read_text(encoding="utf-8"))
    SOURCES[_file] = _source(_file, _front["source"])
    VERSIONS[_document] = str(_front["version"])
    SECTIONS.extend(_sections(_document, _file, _body))

FIELDS = {r["record"]: r for r in _loaded["data_dictionary.yaml"]["records"]}
TERMS = _loaded["glossary.yaml"]["terms"]
PLANS = {p["plan"]: p for p in _loaded["plans.yaml"]["plans"]}
DEDUCTIBLE_APPLIES_TO = _loaded["plans.yaml"]["deductible_applies_to"]
ADD_ONS = _loaded["plans.yaml"]["add_ons"]
RULES = _loaded["claim_rules.yaml"]["rules"]
REFERENCE = [{"topic": t["topic"], "description": t["description"], "fact": " ".join(str(f).split())}
             for t in _loaded["reference_data.yaml"]["topics"] for f in t["facts"]]
