from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _texts(folder):
    for path in folder.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".ts", ".tsx", ".js", ".md", ".css", ".html"}:
            yield path, path.read_text(encoding="utf-8", errors="ignore").lower()


def test_the_agent_never_reads_the_evals_or_the_answer_key():
    offenders = [str(p.relative_to(ROOT)) for p, text in _texts(ROOT / "backend" / "agent")
                 if "evals" in text or "answer_key" in text or "security/" in text]
    assert offenders == []


def test_only_seeding_reads_data_evals_and_only_scoring_reads_the_answer_key():
    evals, key = [], []
    for folder in (ROOT / "backend" / "app", ROOT / "frontend" / "src"):
        for path, text in _texts(folder):
            name = path.name
            if ('"evals"' in text or "evals/" in text or "security/" in text) and name != "seed.py":
                evals.append(name)
            if "answer_key" in text and name not in ("db.py", "seed.py", "scoring.py"):
                key.append(name)
    assert (evals, key) == ([], [])


def test_instructions_quote_no_claim():
    import re

    for name in ("instructions_claim.md",):
        text = (ROOT / "backend" / "agent" / name).read_text(encoding="utf-8")
        assert not re.search(r"CLM-\d{4}", text), name
