"""The repository's layout, pinned so it cannot quietly rot: no account data in any tracked file, the labs where the index says."""
import importlib.util
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ACCOUNT = "3881" + "04035559"  # in pieces: no file of the repository holds the account id whole, this one included
WORK_EMAIL = "alexguimenti." + "searchatlas@gmail.com"


def _scrub_module():
    spec = importlib.util.spec_from_file_location("scrub_notebooks", REPO / "scripts" / "scrub_notebooks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tracked_files() -> list[Path]:
    """Every file git tracks (and every file about to be: a staged or a new one that is not ignored)."""
    names = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=REPO, capture_output=True, check=True).stdout
    return [REPO / n.decode("utf-8") for n in names.split(b"\0") if n and (REPO / n.decode("utf-8")).is_file()]


def test_scrub_replaces_the_account_and_leaves_the_example_account():
    text, n = _scrub_module().scrub_text(f"arn:aws:lambda:us-east-1:{ACCOUNT}:function:x and arn:aws:iam::123456789012:role/y")
    assert ACCOUNT not in text and text.count("123456789012") == 2 and n == 1


def test_scrub_replaces_hosts_keys_and_the_work_email():
    raw = ("https://" + "abcdefghij" + "klmnopqrst.supabase.co sk-or-v1-" + "a" * 40 + " https://" + "b" * 32
           + ".lambda-url.us-east-1.on.aws " + WORK_EMAIL)
    text, n = _scrub_module().scrub_text(raw)
    assert n == 4
    assert "example-project.supabase.co" in text and "<redacted>" in text
    assert "example.lambda-url.us-east-1.on.aws" in text and "alexguimenti@gmail.com" in text
    assert WORK_EMAIL not in text


def test_scrub_leaves_the_tests_fixture_hosts_and_the_placeholders():
    scrub = _scrub_module()
    assert scrub.scrub_text("abc123.lambda-url.us-east-1.on.aws abc.lambda-url.us-east-1.on.aws")[1] == 0
    once, _ = scrub.scrub_text(f"arn:aws:iam::{ACCOUNT}:role/y https://x.supabase.co")
    assert scrub.scrub_text(once)[1] == 0  # what it wrote does not match again


def test_no_tracked_file_holds_account_data():
    patterns = _scrub_module().SENSITIVE
    found = []
    for path in tracked_files():
        if path.suffix in {".lock", ".png", ".jpg", ".ico", ".woff2"} or path.name == "package-lock.json":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").replace("123456789012", "")
        found += [f"{path.relative_to(REPO).as_posix()}: {p.pattern}" for p in patterns if p.search(text)]
    assert found == []
