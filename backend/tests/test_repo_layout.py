"""The repository's layout, pinned so it cannot quietly rot: no account data in any tracked file, the labs where the index says."""
import importlib.util
import json
import re
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


# ---- the labs ----

GENERATED = ("memory_data", "qdrant_data", "build", "dashboard/dist", ".env", "agentcore/.env.local")  # made by running the lab, or secrets
NOT_A_PATH = {"../x"}  # a path-traversal input a lab's security check feeds to its tool


def lab_notebooks() -> list[Path]:
    return sorted((REPO / "labs").rglob("*.ipynb"))


def relative_paths(notebook: Path) -> list[str]:
    """Every string literal in a code cell that is ".." or starts with "../": a folder the lab reads, relative to where it lives."""
    cells = json.loads(notebook.read_text(encoding="utf-8"))["cells"]
    source = "\n".join("".join(c["source"]) for c in cells if c["cell_type"] == "code")
    return [m.group(2) for m in re.finditer(r"""(["'])(\.\.(?:/[^"'\n]*)?)\1""", source) if m.group(2) not in NOT_A_PATH]


def test_every_relative_path_in_a_lab_notebook_exists():
    missing = []
    for notebook in lab_notebooks():
        for literal in relative_paths(notebook):
            target = (notebook.parent / literal).resolve().as_posix()
            if not Path(target).exists() and not any(target.endswith(g) for g in GENERATED):
                missing.append(f"{notebook.relative_to(REPO).as_posix()}: {literal}")
    assert missing == []


def test_the_labs_live_by_week_and_day():
    assert lab_notebooks() and all(re.fullmatch(r"week\d/day\d", n.parent.relative_to(REPO / "labs").as_posix()) for n in lab_notebooks())
    assert not (REPO / "undercoverlab").exists()
    assert not (REPO / "labs/week3/day4/undercoverlab/.git").exists() and (REPO / "labs/week3/day4/undercoverlab/app/agent/main.py").is_file()


def test_the_lab_desks_and_the_helpers_are_with_their_labs():
    for path in ("labs/week1/day5/lab1_desk", "labs/week1/day5/lab2_desk", "labs/week2/day2/knowledge_mcp.py", "labs/week3/monitor.py", "labs/week3/week3day4_aws.py"):
        assert (REPO / path).exists(), path
    assert not (REPO / "lab1_desk").exists() and not (REPO / "lab2_desk").exists() and not (REPO / "monitor.py").exists()


def test_the_lab_scripts_find_the_repository_root_from_where_they_now_live():
    for name, pattern in (("labs/week3/week3day4_aws.py", r"REPO = Path\(__file__\)\.resolve\(\)\.parents\[(\d)\]"),
                          ("labs/week2/day2/knowledge_mcp.py", r"Path\(__file__\)\.resolve\(\)\.parents\[(\d)\] / \"qdrant_data\"")):
        script = REPO / name
        match = re.search(pattern, script.read_text(encoding="utf-8"))
        assert match, f"{name} does not climb to the repository root with parents[N]"
        assert script.resolve().parents[int(match.group(1))] == REPO, name


# ---- the index and the root ----

ROOT = {".gitignore", ".mcp.json", ".vscode", "README.md", "COMMANDS.md", "package.json", "agent.toml",
        "agentcore", "backend", "data", "docs", "e2e", "frontend", "labs", "runtime", "scripts"}


def test_the_labs_index_names_every_notebook_and_links_to_it():
    index = (REPO / "labs" / "README.md").read_text(encoding="utf-8")
    missing = [n.name for n in lab_notebooks() if f"]({n.relative_to(REPO / 'labs').as_posix()})" not in index]
    assert missing == []


def test_the_root_holds_only_the_project():
    assert {p.relative_to(REPO).parts[0] for p in tracked_files()} == ROOT


# ---- the docs ----

REFERENCE_DOCS = {"README.md", "TECH_STACK.md", "CODE_STANDARDS.md", "PROCESS.md", "DESIGN.md", "STATUS.md", "DECISIONS.md", "PLAN_REORG.md"}
SPECS = {"SPEC.md", "HARNESS.md", "DURABLE.md", "SWARM.md", "IMPROVE.md", "SECURE.md", "CONTEXT.md", "RUNTIME.md", "COPILOT.md"}
TEXT_SUFFIXES = {".md", ".py", ".ts", ".tsx", ".mjs", ".css", ".json", ".toml", ".yaml"}
ALIASES = {"docs/BRAND.md"}  # the course's specs call our docs/DESIGN.md "BRAND.md"; the plans say so
ABOUT_ITSELF = {"PLAN_REORG.md"}  # the reorganization plan (wherever it is) names the paths it is about to make


def test_the_docs_are_split_into_specs_and_plans():
    top = {p.name for p in (REPO / "docs").glob("*.md")}
    assert top <= REFERENCE_DOCS, sorted(top - REFERENCE_DOCS)
    assert {p.name for p in (REPO / "docs/specs").glob("*.md")} == SPECS
    assert not [p.name for p in (REPO / "docs/plans").glob("*.md") if not (p.name.startswith("PLAN") or p.name == "CLEANUP.md")]


def test_every_docs_path_the_repo_mentions_exists():
    named = re.compile(r"docs/[A-Za-z0-9_/.-]+\.(?:md|html)")
    missing = [f"{p.relative_to(REPO).as_posix()}: {m}" for p in tracked_files() if p.suffix in TEXT_SUFFIXES
               for m in named.findall(p.read_text(encoding="utf-8", errors="ignore")) if not (REPO / m).exists() and m not in ALIASES
               and p.name != "PLAN_REORG.md"]
    assert missing == []


def test_every_relative_link_in_the_docs_exists():
    link = re.compile(r"\]\(([^)\s]+)\)")
    missing = []
    for doc in (d for d in (REPO / "docs").rglob("*.md") if d.name not in ABOUT_ITSELF):
        for target in link.findall(doc.read_text(encoding="utf-8")):
            if target.startswith(("http:", "https:", "mailto:", "#")):
                continue
            if not (doc.parent / target.split("#")[0]).exists():
                missing.append(f"{doc.relative_to(REPO).as_posix()}: {target}")
    assert missing == []


def test_the_docs_index_links_every_spec_and_plan():
    index = (REPO / "docs" / "README.md").read_text(encoding="utf-8")
    listed = [p.relative_to(REPO / "docs").as_posix() for folder in ("specs", "plans") for p in sorted((REPO / "docs" / folder).glob("*.md"))]
    assert listed and [name for name in listed if f"]({name})" not in index] == []
