"""The policy wording and the claims manual, kept in the documents table and cut into their numbered sections. Seeding fills
the table from data governance's files; nothing else reads them."""
import json
import re
from dataclasses import dataclass

import yaml
from sqlalchemy import Engine, select

from app.db import documents

# The documents the agent may look up, by the name it uses for them, and the file each is kept in.
DOCUMENT_FILES = {"policy_wording": "policy_wording.md", "claims_manual": "claims_manual.md"}

_HEADING = re.compile(r"^## (\d+)\. (.+)$", re.MULTILINE)
_FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


@dataclass(frozen=True)
class Section:
    number: int
    title: str
    text: str


def split_front_matter(text: str) -> tuple[dict, str]:
    """A document's front matter (its source and version) and what follows it."""
    found = _FRONT_MATTER.match(text)
    if not found:
        return {}, text
    return yaml.safe_load(found[1]), text[found.end():]


class Documents:
    """The documents of one database, each cut into sections the first time it is asked for."""

    def __init__(self, engine: Engine):
        self._engine = engine
        self._cache: dict[str, list[Section]] = {}

    def _row(self, document: str):
        if document not in DOCUMENT_FILES:
            raise KeyError(f"unknown document {document!r}")
        with self._engine.connect() as conn:
            row = conn.execute(select(documents).where(documents.c.document == document)).mappings().first()
        if row is None:
            raise KeyError(f"{document} has not been seeded")
        return row

    def sections(self, document: str) -> list[Section]:
        if document not in self._cache:
            text = self._row(document)["text"]
            heads = list(_HEADING.finditer(text))
            self._cache[document] = [
                Section(int(h[1]), h[2].strip(), text[h.start(): heads[i + 1].start() if i + 1 < len(heads) else len(text)].strip())
                for i, h in enumerate(heads)
            ]
        return self._cache[document]

    def section(self, document: str, number: int) -> Section:
        for s in self.sections(document):
            if s.number == number:
                return s
        raise KeyError(f"{document} has no section {number}")

    def version(self, document: str) -> str:
        return self._row(document)["version"]

    def source(self, document: str) -> dict:
        return json.loads(self._row(document)["source"])
