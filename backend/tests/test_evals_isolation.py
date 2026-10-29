from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_no_app_code_references_evals():
    offenders = []
    for folder in (ROOT / "backend" / "app", ROOT / "frontend" / "src"):
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".ts", ".tsx", ".js", ".md", ".css", ".html"}:
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
                if "evals" in text or "security/" in text:
                    offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
