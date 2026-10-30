import re
from dataclasses import dataclass
from functools import lru_cache

from app.reference import Reference

# The documents the agent may look up, by the name it uses for them.
DOCUMENTS = {"policy_wording": "policy_wording.md", "claims_manual": "claims_manual.md"}

_HEADING = re.compile(r"^## (\d+)\. (.+)$", re.MULTILINE)
_VERSION = re.compile(r"Version (\d{4}\.\d+)")
SNIPPET_CHARS = 160


@dataclass(frozen=True)
class Section:
    number: int
    title: str
    text: str


class Documents:
    """The policy wording and the claims manual, cut into their numbered sections."""

    def __init__(self, ref: Reference):
        self._ref = ref
        self._cache: dict[str, list[Section]] = {}

    def _text(self, document: str) -> str:
        if document not in DOCUMENTS:
            raise KeyError(f"unknown document {document!r}")
        return self._ref.read(DOCUMENTS[document])

    def sections(self, document: str) -> list[Section]:
        if document not in self._cache:
            text = self._text(document)
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

    def search(self, query: str, limit: int = 5) -> list[dict]:
        term = query.lower().strip()
        if not term:
            return []
        hits = []
        for document in DOCUMENTS:
            for s in self.sections(document):
                lowered = s.text.lower()
                count = lowered.count(term)
                if count:
                    at = lowered.index(term)
                    start = max(0, at - SNIPPET_CHARS // 2)
                    snippet = " ".join(s.text[start: start + SNIPPET_CHARS].split())
                    hits.append((count, {"document": document, "section": s.number, "title": s.title, "snippet": snippet}))
        hits.sort(key=lambda h: -h[0])
        return [h for _, h in hits[:limit]]

    def version(self, document: str) -> str:
        match = _VERSION.search(self._text(document))
        return match[1] if match else "unknown"


@lru_cache(maxsize=None)
def documents_for(ref: Reference) -> Documents:
    """One section index per Reference, so the documents are cut into sections once."""
    return Documents(ref)
