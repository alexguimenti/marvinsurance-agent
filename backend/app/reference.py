import json
from pathlib import Path

ALLOWED_FILES = frozenset(
    {
        "claims.json",
        "policies.json",
        "customers.json",
        "handlers.json",
        "company.json",
        "improvement.json",
        "models.json",
    }
)


# The security team's claim, with its policy and customer, sits in a folder of its own and joins the queue's own files
# when they are read. Its handler's decision beside them is never read: it stays out of every evaluation set.
SECURITY_MERGED = ("claims.json", "policies.json", "customers.json")


class Reference:
    """Read-only access to the whitelisted files in data/."""

    def __init__(self, data_dir: str):
        self._dir = Path(data_dir)
        self._cache: dict[str, object] = {}

    @property
    def data_dir(self) -> Path:
        return self._dir

    def read(self, name: str):
        if name not in ALLOWED_FILES:
            raise ValueError(f"{name!r} is not an allowed reference file")
        if name not in self._cache:
            text = (self._dir / name).read_text(encoding="utf-8")
            value = text if name.endswith(".md") else json.loads(text)
            if name in SECURITY_MERGED:
                value = value + json.loads((self._dir / "security" / name).read_text(encoding="utf-8"))
            self._cache[name] = value
        return self._cache[name]

    def policy(self, policy_id: str) -> dict:
        return self._find("policies.json", "policy_id", policy_id)

    def customer(self, customer_id: str) -> dict:
        return self._find("customers.json", "customer_id", customer_id)

    def _find(self, name: str, key: str, value: str) -> dict:
        for row in self.read(name):
            if row[key] == value:
                return row
        raise LookupError(f"no {key} {value} in {name}")

    @property
    def company(self) -> dict:
        return self.read("company.json")

    @property
    def handlers(self) -> list[dict]:
        return self.read("handlers.json")

    @property
    def claims(self) -> list[dict]:
        return self.read("claims.json")
