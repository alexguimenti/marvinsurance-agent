import json
from pathlib import Path

ALLOWED_FILES = frozenset(
    {
        "claims.json",
        "policies.json",
        "customers.json",
        "plans.json",
        "handlers.json",
        "company.json",
        "knowledge.json",
        "policy_wording.md",
        "claims_manual.md",
    }
)


class Reference:
    """Read-only access to the whitelisted files in data/."""

    def __init__(self, data_dir: str):
        self._dir = Path(data_dir)
        self._cache: dict[str, object] = {}

    def read(self, name: str):
        if name not in ALLOWED_FILES:
            raise ValueError(f"{name!r} is not an allowed reference file")
        if name not in self._cache:
            text = (self._dir / name).read_text(encoding="utf-8")
            self._cache[name] = text if name.endswith(".md") else json.loads(text)
        return self._cache[name]

    def policy(self, policy_id: str) -> dict:
        return next(p for p in self.read("policies.json") if p["policy_id"] == policy_id)

    def customer(self, customer_id: str) -> dict:
        return next(c for c in self.read("customers.json") if c["customer_id"] == customer_id)

    def plan(self, name: str) -> dict:
        return next(p for p in self.read("plans.json")["plans"] if p["plan"] == name)

    @property
    def add_ons(self) -> list[dict]:
        return self.read("plans.json")["add_ons"]

    @property
    def company(self) -> dict:
        return self.read("company.json")

    @property
    def handlers(self) -> list[dict]:
        return self.read("handlers.json")

    @property
    def wording(self) -> str:
        return self.read("policy_wording.md")

    @property
    def manual(self) -> str:
        return self.read("claims_manual.md")

    @property
    def knowledge(self) -> list[dict]:
        return self.read("knowledge.json")

    @property
    def claims(self) -> list[dict]:
        return self.read("claims.json")
