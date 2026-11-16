# Reorganizing the repository for a portfolio and a reference: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the repository readable from outside as a portfolio, and easy to mine for ideas when building the next project: labs by week and day, docs split into specs and plans, a clean root, a README that maps "to see X, open Y", no account data or secrets anywhere in the files or the history.

**Architecture:** A light reorganization. The project's code (`backend/`, `frontend/`, `e2e/`, `agentcore/`, `runtime/`, `scripts/`, `data/`, `agent.toml`) stays where it is, so nothing in the build, the tests or the deploy moves. What moves is the material around it: the labs, the docs and the root's leftovers. A small set of repository tests (`backend/tests/test_repo_layout.py`) pins the new layout so it cannot quietly rot: every path a lab uses exists, every doc path the repo mentions exists, the root holds only what it should, and no tracked file holds account data. The git history is rewritten last, once everything else is committed.

**Tech Stack:** Python 3.12 (pytest, run from `backend/` with `uv run`), Jupyter notebooks as JSON, git, `git-filter-repo`.

**Spec:** the six decisions taken with the owner on 2026-11-16, copied verbatim under Global Constraints. There is no other spec document.

## Global Constraints

- Purpose: a public portfolio and a personal reference base. Nobody is expected to run it; the owner reads it to borrow ideas.
- Labs: keep all of them, organized as `labs/weekN/dayN/`, with an index (`labs/README.md`) saying what each teaches. Notebook outputs are scrubbed of account data before they are committed.
- `undercoverlab/` (a nested git repository from the Week 3 Day 4 lab) becomes a normal folder `labs/week3/day4/undercoverlab/` holding only its 19 tracked files: no `agentcore/.cache/`, no inner `.git`.
- Root leftovers: delete the generated ones (`build/`, `e2e/shots/`, `dashboard/`, `marvin.db`, `memory_data/`, `qdrant_data/`, `.playwright-mcp/`, `.tmp/`) but keep `.superpowers/`. `lab1_desk/`, `lab2_desk/` and `monitor.py` move into the labs.
- History: rewrite it with `git filter-repo`: remove `.env` from every commit, and make `alexguimenti@gmail.com` the author and committer of every commit (it replaces the work e-mail every commit has now: `git log -1 --format=%ae` prints it). The OpenRouter key that was in `.env` is already revoked.
- Depth: light. The project's code folders stay in place. Medium and deep restructuring are listed in Phase 2, optional, not done here.
- Must keep working: the backend suite (817 passed, 1 skipped before this plan, plus this plan's new tests), `npm run check` as one command, and `agentcore deploy`.
- Docs are written in English, as the rest of the docs.
- Placeholder for an AWS account id in scrubbed files: `123456789012` (AWS's documented example).

## Review Focus

1. **A lab that no longer opens its files after the move.** A notebook that read `../data` from `labs/` must read the same folder from `labs/weekN/dayN/`. Pinned by `test_every_relative_path_in_a_lab_notebook_exists` (Task 2).
2. **Account data that survives in an output cell, or in the history.** Pinned by `test_no_tracked_file_holds_account_data` (Task 1) for the files, and by the history checks in Task 7.
3. **A link in a doc or a code comment that points at a doc that moved.** `e2e/live_security.mjs` already points at `docs/PLAN_SECURE.md`, which is in `docs/plans/`. Pinned by `test_every_docs_path_the_repo_mentions_exists` (Task 4).
4. **Something the build or the deploy reads that a cleanup deleted.** `agent.toml`, `data/`, `runtime/`, `agentcore/agentcore.json` and `agentcore/cdk/` stay. Pinned by `test_the_root_holds_only_the_project` (Task 3) and by `npm run check` in a clean worktree (Task 6).
5. **The next commits still made with the old e-mail.** Task 7 sets this repository's `user.email` and checks it.

---

### Task 1: No account data in any file

**Files:**
- Create: `scripts/scrub_notebooks.py`
- Create: `backend/tests/test_repo_layout.py`
- Modify: `labs/week3day1.ipynb`, `labs/week3day2_mex2.ipynb`, `labs/week3day3.ipynb`, `labs/week3day4_mex_lab1.ipynb` (tracked), and add the untracked `labs/week3day4_aws.py`, `labs/week3day4_mex_lab2.ipynb`, `labs/week3day5_mex_lab1.ipynb`, `labs/week3day5_mex_lab2.ipynb`

**Interfaces:**
- Produces: `scrub_text(text: str) -> tuple[str, int]` in `scripts/scrub_notebooks.py` (the scrubbed text and how many values it replaced); `python scripts/scrub_notebooks.py PATH...` rewrites each file in place and prints `PATH: N replaced`. `SENSITIVE: list[re.Pattern]` in the same module, the patterns Task 7 also feeds to `git filter-repo --replace-text`.
- Produces: `REPO = Path(__file__).resolve().parents[2]` and `tracked_files() -> list[Path]` (from `git ls-files`) in `backend/tests/test_repo_layout.py`; later tasks add their tests to this file.

What is sensitive (each with its replacement):
- the owner's AWS account id (`aws sts get-caller-identity --query Account --output text` prints it) anywhere, and any 12-digit account inside an ARN (`arn:aws:[a-z0-9-]+:[a-z0-9-]*:(\d{12}):`) other than `123456789012` → `123456789012`
- a Supabase project host `[a-z]{20}\.supabase\.(co|com)` → `example-project.supabase.co`
- a Lambda Function URL host `[a-z0-9]{32}\.lambda-url\.[a-z0-9-]+\.on\.aws` → `example.lambda-url.us-east-1.on.aws` (the tests' own `abc.` and `abc123.` hosts are not 32 characters and stay)
- a CloudFront host `[a-z0-9]{13,14}\.cloudfront\.net` → `example.cloudfront.net`
- an OpenRouter key `sk-or-v1-[A-Za-z0-9]{20,}`, a JWT `eyJhbGciOi[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_.-]+`, a `postgres(ql)?://user:password@host` with a password → `<redacted>`
- the work e-mail (the one `git log -1 --format=%ae` prints) → `alexguimenti@gmail.com`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_repo_layout.py
def test_scrub_replaces_the_account_and_leaves_the_example_account():
    text, n = scrub_text(f"arn:aws:lambda:us-east-1:{ACCOUNT}:function:x and arn:aws:iam::123456789012:role/y")
    assert ACCOUNT not in text and text.count("123456789012") == 2 and n == 1

def test_scrub_replaces_hosts_keys_and_the_work_email():
    raw = ("https://" + "abcdefghij" + "klmnopqrst.supabase.co sk-or-v1-" + "a" * 40 + " https://" + "b" * 32
           + ".lambda-url.us-east-1.on.aws " + WORK_EMAIL)
    text, n = scrub_text(raw)
    assert n == 4 and "example-project.supabase.co" in text and "<redacted>" in text
    assert "example.lambda-url.us-east-1.on.aws" in text and "alexguimenti@gmail.com" in text

def test_scrub_leaves_the_tests_fixture_hosts():
    assert scrub_text("abc123.lambda-url.us-east-1.on.aws")[1] == 0

def test_no_tracked_file_holds_account_data():
    found = [f"{p.relative_to(REPO)}: {pat.pattern}" for p in tracked_files() if p.suffix not in {".lock", ".png", ".jpg"}
             for pat in SENSITIVE if pat.search(p.read_text(encoding="utf-8", errors="ignore").replace("123456789012", ""))]
    assert found == []
```

`ACCOUNT` and `WORK_EMAIL` are built in the test from pieces (`"3881" + ...`), so that no file in the repository holds them whole. `scrub_text` is imported in the test with `importlib.util.spec_from_file_location("scrub_notebooks", REPO / "scripts" / "scrub_notebooks.py")`; `SENSITIVE` patterns must not match the placeholders.

- [ ] **Step 2: Run them to watch them fail**

Run: `cd backend; uv run pytest tests/test_repo_layout.py -q`
Expected: FAIL: the module `scripts/scrub_notebooks.py` does not exist.

- [ ] **Step 3: Write `scripts/scrub_notebooks.py`** with `SENSITIVE`, `scrub_text` and the command line above. It works on the file's text (a notebook is JSON, so the text replacement keeps it valid); it never prints a matched value, only counts.

- [ ] **Step 4: Run the unit tests**

Run: `cd backend; uv run pytest tests/test_repo_layout.py -q -k scrub`
Expected: 3 passed. `test_no_tracked_file_holds_account_data` still fails, naming the four tracked Week 3 notebooks.

- [ ] **Step 5: Scrub the labs and add the untracked ones**

Run: `python scripts/scrub_notebooks.py labs/*.ipynb labs/week3day4_aws.py`, then `git add labs/week3day4_aws.py labs/week3day4_mex_lab2.ipynb labs/week3day5_mex_lab1.ipynb labs/week3day5_mex_lab2.ipynb`.
Expected: the four Week 3 Day 4/5 files and the four tracked Week 3 notebooks report replacements (`week3day5_mex_lab2.ipynb` about 60); each notebook still opens (`python -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))" FILE` for each).

- [ ] **Step 6: Run the whole file**

Run: `cd backend; uv run pytest tests/test_repo_layout.py -q`
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add scripts/scrub_notebooks.py backend/tests/test_repo_layout.py labs/
git commit -m "chore: scrub account data from the labs, and a test that keeps it out"
```

---

### Task 2: The labs by week and day

**Files:**
- Move (with `git mv`): every file in `labs/` to the folder below; `lab1_desk/` and `lab2_desk/` to `labs/week1/day5/`; `monitor.py` to `labs/week3/monitor.py`; `labs/week3day4_aws.py` to `labs/week3/week3day4_aws.py`
- Create: `labs/week3/day4/undercoverlab/` from the nested repository's 19 tracked files
- Modify: the moved notebooks' code cells (relative paths, the `week3day4_aws` import); `.gitignore`
- Test: `backend/tests/test_repo_layout.py`

| Folder | What goes in it |
|---|---|
| `labs/week1/day2/` | `week1day2_mex.ipynb` |
| `labs/week1/day4/` | `week1day4_mex.ipynb` |
| `labs/week1/day5/` | `week1day5_mex.ipynb`, `week1day5_mex_2.ipynb`, `lab1_desk/`, `lab2_desk/` |
| `labs/week2/day1/` | `week2day1_mex.ipynb` |
| `labs/week2/day2/` | `week2day2_mex.ipynb`, `knowledge_mcp.py` |
| `labs/week2/day3/` | `week2day3_mex.ipynb` |
| `labs/week2/day4/` | `week2day4.ipynb`, `week2day4_mex.ipynb` |
| `labs/week2/day5/` | `week2day5.ipynb`, `week2day5_mex.ipynb` |
| `labs/week3/` | `week3day4_aws.py` (used by Day 4 and Day 5), `monitor.py` |
| `labs/week3/day1/` | `week3day1.ipynb` |
| `labs/week3/day2/` | `week3day2_mex.ipynb`, `week3day2_mex2.ipynb` |
| `labs/week3/day3/` | `week3day3.ipynb` |
| `labs/week3/day4/` | `week3day4_mex_lab1.ipynb`, `week3day4_mex_lab2.ipynb`, `undercoverlab/` |
| `labs/week3/day5/` | `week3day5_mex_lab1.ipynb`, `week3day5_mex_lab2.ipynb` |

**Interfaces:**
- Consumes: `REPO`, `tracked_files()` from Task 1.
- Produces: `lab_notebooks() -> list[Path]` (every `labs/**/*.ipynb`) in `test_repo_layout.py`, used by Task 3.

- [ ] **Step 1: Write the failing test**

```python
GENERATED = {"memory_data", "qdrant_data", "build", "dashboard/dist", ".env", "agentcore/.env.local"}  # made by running the lab

def test_every_relative_path_in_a_lab_notebook_exists():
    missing = []
    for nb in lab_notebooks():
        for literal in relative_paths(nb):  # every string literal in a code cell equal to ".." or starting with "../"
            target = (nb.parent / literal).resolve()
            if not target.exists() and not any(str(target).replace("\\", "/").endswith(g) for g in GENERATED):
                missing.append(f"{nb.relative_to(REPO)}: {literal}")
    assert missing == []

def test_the_labs_live_by_week_and_day():
    assert all(re.fullmatch(r"week\d/day\d", nb.parent.relative_to(REPO / "labs").as_posix()) for nb in lab_notebooks())
    assert not (REPO / "undercoverlab").exists() and not (REPO / "labs/week3/day4/undercoverlab/.git").exists()
```

- [ ] **Step 2: Run it to watch it fail**

Run: `cd backend; uv run pytest tests/test_repo_layout.py -q -k labs`
Expected: FAIL: the notebooks are in `labs/`, not `labs/weekN/dayN/`.

- [ ] **Step 3: Move the files** with `git mv` as in the table. The desks' `.venv` folders are not tracked and do not survive a move (a venv holds absolute paths): delete `lab1_desk/.venv` and `lab2_desk/.venv` before moving the folders; `requirements.txt` in each desk rebuilds them.

- [ ] **Step 4: Bring in `undercoverlab`.** `git rm --cached undercoverlab` (the gitlink), copy its 19 tracked files (`git -C undercoverlab ls-files`) to `labs/week3/day4/undercoverlab/` keeping their paths, delete the old `undercoverlab/` folder (with its `.git` and its `agentcore/.cache/`), and `git add labs/week3/day4/undercoverlab`. Remove the `undercoverlab/` line and its comment from `.gitignore`; change `agentcore/.env.local` to `**/agentcore/.env.local`; add `agentcore/.cache/`, `agentcore/.cli/`, `agentcore/aws-targets.json` and `**/agentcore/.cache/` (the CLI writes them, with the account id). Scrub the copied files with `scripts/scrub_notebooks.py` (its `aws-targets.json` holds the account id; keep it out instead: it matches the new ignore line).

- [ ] **Step 5: Fix the paths in the moved notebooks.** In code cells only (`json` load, edit `source`, dump with `indent=1` and `ensure_ascii=False`, as Jupyter writes):
  - `".."` becomes `"../../.."`, and a literal starting `"../` becomes `"../../../` (the same for single quotes).
  - `week3day4_mex_lab1.ipynb`: `"../undercoverlab/agentcore/.env.local"` becomes `"undercoverlab/agentcore/.env.local"`.
  - `import week3day4_aws as aws` becomes `import sys; sys.path.insert(0, ".."); import week3day4_aws as aws`.
  - `uv run monitor.py` in text becomes `uv run labs/week3/monitor.py`.
  The desks' notebooks find their desk by walking up from the current folder, and `load_dotenv` finds the root `.env` the same way: neither changes.

- [ ] **Step 6: Run the tests**

Run: `cd backend; uv run pytest tests/test_repo_layout.py -q`
Expected: all passed.

- [ ] **Step 7: Commit**

```bash
git add -A labs lab1_desk lab2_desk monitor.py undercoverlab .gitignore backend/tests/test_repo_layout.py
git commit -m "chore: the labs by week and day; the Week 3 Day 4 lab project as a plain folder"
```

---

### Task 3: The labs' index, and a clean root

**Files:**
- Create: `labs/README.md`
- Delete (not tracked, from the disk): `build/`, `e2e/shots/`, `dashboard/`, `marvin.db`, `memory_data/`, `qdrant_data/`, `.playwright-mcp/`, `.tmp/`
- Modify: `.gitignore` (add `e2e/shots/`; `dashboard/` and `marvin.db` stay ignored, since `npm run dev` and an old flow can make them again)
- Test: `backend/tests/test_repo_layout.py`

**Interfaces:**
- Consumes: `lab_notebooks()` from Task 2.

`labs/README.md` is one table, a row per notebook, in course order: week and day, the notebook (a relative link), one line on what it teaches, and the part of the project it led to (a path such as `backend/agent/dispatcher.py`). It marks the course's own notebooks (no `_mex` in the name: `week2day4.ipynb`, `week2day5.ipynb`, `week3day1.ipynb`, `week3day3.ipynb`) as "the course's", and the rest as "mine". A short paragraph above the table says the notebooks are as they ran during the course, with account data replaced by placeholders, and that each reads the root `.env`.

- [ ] **Step 1: Write the failing tests**

```python
ROOT = {".gitignore", ".mcp.json", ".vscode", "README.md", "COMMANDS.md", "package.json", "agent.toml",
        "agentcore", "backend", "data", "docs", "e2e", "frontend", "labs", "runtime", "scripts"}

def test_the_labs_index_names_every_notebook():
    index = (REPO / "labs" / "README.md").read_text(encoding="utf-8")
    assert [nb.name for nb in lab_notebooks() if nb.name not in index] == []

def test_the_root_holds_only_the_project():
    assert {p.relative_to(REPO).parts[0] for p in tracked_files()} == ROOT
```

- [ ] **Step 2: Run them to watch them fail**

Run: `cd backend; uv run pytest tests/test_repo_layout.py -q -k "index or root"`
Expected: FAIL: no `labs/README.md`.

- [ ] **Step 3: Write `labs/README.md`** as described, reading each notebook's first cells to write its line.

- [ ] **Step 4: Delete the generated leftovers** listed under Files, and add `e2e/shots/` to `.gitignore`. Leave `.superpowers/`.

- [ ] **Step 5: Run the tests**

Run: `cd backend; uv run pytest tests/test_repo_layout.py -q`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add labs/README.md .gitignore
git commit -m "docs: an index of the labs; the root holds only the project"
```

---

### Task 4: The docs split into specs and plans

**Files:**
- Move (with `git mv`) to `docs/specs/`: `SPEC.md`, `plans/HARNESS.md`, `DURABLE.md`, `SWARM.md`, `IMPROVE.md`, `SECURE.md`, `CONTEXT.md`, `RUNTIME.md`, `COPILOT.md`
- Move to `docs/plans/`: `PLAN_DURABLE.md`, `PLAN_SWARM.md`, `PLAN_IMPROVE.md`
- Stay in `docs/`: `TECH_STACK.md`, `CODE_STANDARDS.md`, `PROCESS.md`, `DESIGN.md`, `STATUS.md`, `diagrams/`, and this plan until it is done
- Create: `docs/README.md`
- Modify: every file that names a moved doc (found by the test)
- Test: `backend/tests/test_repo_layout.py`

`docs/README.md`: one paragraph on how the docs work (a spec says what to build and why; its plan says how, task by task; `STATUS.md` says where it stands), then a table in the order the project was built: the step, its spec, its plan, one line on what it added. Pilot (`specs/SPEC.md`, `plans/PLAN.md`), Harness, Durable, Swarm, Improve, Secure, Context, Runtime, Copilot, and the cleanup pass (`plans/CLEANUP.md`, no spec). Then the reference docs (`TECH_STACK.md`, `CODE_STANDARDS.md`, `PROCESS.md`, `DESIGN.md`, `DECISIONS.md`, `diagrams/`).

- [ ] **Step 1: Write the failing test**

```python
def test_every_docs_path_the_repo_mentions_exists():
    named = re.compile(r"docs/[A-Za-z0-9_/.-]+\.(?:md|html)")
    missing = [f"{p.relative_to(REPO)}: {m}" for p in tracked_files() if p.suffix in {".md", ".py", ".ts", ".tsx", ".mjs", ".css", ".json"}
               for m in named.findall(p.read_text(encoding="utf-8", errors="ignore")) if not (REPO / m).exists()]
    assert missing == []
```

Relative links inside `docs/` (such as `[...](diagrams/copilot_graph.html)`) are checked too: for each `](target)` in a `docs/**/*.md` whose target is not a URL or an anchor, `(doc.parent / target)` exists.

- [ ] **Step 2: Run it to watch it fail**

Run: `cd backend; uv run pytest tests/test_repo_layout.py -q -k docs`
Expected: FAIL, naming at least `e2e/live_security.mjs: docs/PLAN_SECURE.md` (already stale today).

- [ ] **Step 3: Move the docs** with `git mv` as listed, and write `docs/README.md`.

- [ ] **Step 4: Fix every path the test names**, file by file, to the doc's new place. Inside the moved specs and plans, fix the relative links the same way. Do not reword anything else.

- [ ] **Step 5: Run the tests**

Run: `cd backend; uv run pytest tests/test_repo_layout.py -q`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add -A docs README.md e2e frontend backend scripts
git commit -m "docs: specs and plans in their own folders, with an index"
```

---

### Task 5: The README for a reader from outside, and the decisions

**Files:**
- Modify: `README.md` (rewritten)
- Create: `docs/DECISIONS.md`
- Modify: `docs/STATUS.md` (one section on this reorganization)
- Test: `backend/tests/test_repo_layout.py`

`README.md`, in this order, each part short:
1. What it is, in three sentences: a course project (the Forward Deployed Engineer course), a travel-insurance claims desk worked by AI agents, nothing real.
2. What it shows: the claim agent and its dispatcher, the improver, the Copilot, the deploy on AWS (Lambda for the app, AgentCore Runtime for the agents, Gateway, Memory, Identity), a link to `docs/diagrams/copilot_graph.html`.
3. An architecture sketch in a fenced text block: dashboard → app (FastAPI on Lambda) → Supabase; app → AgentCore Runtime (claim agent, Copilot) → context layer through the Gateway; OpenRouter for the models.
4. **Where to find what** ("to see X, open Y"), a table of 12 to 15 rows, each a pattern worth borrowing and the file that shows it, for example: a hand-written LangGraph agent with a human confirmation (`backend/agent/copilot/graph.py`), a dispatcher that is code and no model (`backend/agent/dispatcher.py`), a sign-in that never reaches a checkpoint (`backend/agent/copilot/asker.py`), retrying a request the platform refused (`backend/agent/connection.py`), an AgentCore app with async tasks (`backend/agent/copilot/entrypoint.py`), the deploy (`agentcore/agentcore.json`, `agentcore/cdk/lib/cdk-stack.ts`, `scripts/prepare_runtime.py`), evaluations and a judge, the improver, the context layer over MCP, the design tokens (`frontend/src/styles/tokens.css`), the end-to-end tests (`e2e/tests/`).
5. The course's labs: one line and a link to `labs/README.md`.
6. The docs: one line and a link to `docs/README.md`.
7. Running it locally: three lines that point at `COMMANDS.md` (the current Setup and the `.env` list move into `COMMANDS.md` if they are not there yet).

`docs/DECISIONS.md`: ten to twelve entries, each three lines (the decision, why, where it lives), taken from the specs and `STATUS.md`, not invented: for example the payout limit enforced in the payment code and not by the model, a claim agent per claim and a dispatcher without a model, the handler's sign-in in the graph's context and never in a checkpoint, the confirmation card built by the app from its records, one Runtime session per conversation, polling instead of streaming because the app runs on Lambda, the success criteria computed in code.

- [ ] **Step 1: Write the failing test**

```python
def test_every_path_the_readme_names_exists():
    paths = re.findall(r"`((?:backend|frontend|e2e|agentcore|scripts|labs|docs|data|runtime)/[^`\s]+)`", (REPO / "README.md").read_text(encoding="utf-8"))
    assert len(paths) >= 12 and [p for p in paths if not (REPO / p).exists()] == []
```

- [ ] **Step 2: Run it to watch it fail**

Run: `cd backend; uv run pytest tests/test_repo_layout.py -q -k readme`
Expected: FAIL: today's README names fewer than 12 paths in backticks.

- [ ] **Step 3: Write `README.md`, `docs/DECISIONS.md`** and the `STATUS.md` section as described. Read each file before you name it in the map.

- [ ] **Step 4: Run the tests**

Run: `cd backend; uv run pytest tests/test_repo_layout.py -q`
Expected: all passed (the docs test also covers `DECISIONS.md` and the new README links).

- [ ] **Step 5: Commit**

```bash
git add README.md COMMANDS.md docs
git commit -m "docs: a README for a reader from outside, and the decisions behind the project"
```

---

### Task 6: Everything still builds, tests and deploys

**Files:** none (verification only; a fix found here goes in the task that broke it, with its own commit)

- [ ] **Step 1: The whole check in a clean worktree**

Run: `git worktree add --detach ../undercover_mex_check HEAD`, then in it: `cd backend; uv sync --all-groups --all-extras`, `cd ../frontend; npm ci`, `cd ../e2e; npm ci`, `cd ..; npm run check`.
Expected: the backend suite passes (817 + this plan's tests, 1 skipped), the isolated imports, the front end, type check, lint, and end to end 20 of 20. Then `git worktree remove --force ../undercover_mex_check`.

- [ ] **Step 2: The deploy's packaging**

Run: `python scripts/prepare_runtime.py` from the repo root.
Expected: it writes `runtime/pyproject.toml` as before and names no missing file. (The owner runs `agentcore deploy` when they choose; nothing it reads moved.)

- [ ] **Step 3: Record it** in `docs/STATUS.md` (the counts) and commit: `git commit -am "docs: the reorganized repository passes its check"`.

---

### Task 7: The history, rewritten

This task rewrites every commit. **Stop and ask the owner before Step 3**, showing them the backup's path; run nothing in Step 3 without a yes in this session.

**Files:** none in the tree; the whole history.

- [ ] **Step 1: Install the tool and check the repository has no remote**

Run: `uv tool install git-filter-repo`, then `git remote -v`.
Expected: `git filter-repo --version` prints a version; `git remote -v` prints nothing (nothing was ever pushed, so no clone elsewhere breaks).

- [ ] **Step 2: A full backup outside the repository**

Run: `git clone --mirror . ../undercover_mex-before-rewrite.git`.
Expected: the mirror holds every branch (`git -C ../undercover_mex-before-rewrite.git branch` shows `main`, `feature/marvin-agent-pilot`, `backup/secure-wip`). It holds the revoked key: it stays on this machine and is deleted once the owner is satisfied.

- [ ] **Step 3: Rewrite** (after the owner's yes)

Write two files outside the repository: a mailmap with the line `Alexandre Guimenti <alexguimenti@gmail.com> <OLD>` (`OLD` is what `git log -1 --format=%ae` prints before the rewrite), and a replacements file with one `regex:PATTERN==>REPLACEMENT` line per entry of `SENSITIVE` (Task 1), so the account data in the old versions of the notebooks goes too. Then run:
`git filter-repo --force --invert-paths --path .env --path undercoverlab --mailmap MAILMAP --replace-text REPLACEMENTS`
(`undercoverlab` is the old gitlink; its files live on in `labs/week3/day4/undercoverlab/`.)

- [ ] **Step 4: Check the history**

Run each and compare:
- `git rev-list --all -- .env` → nothing
- `git log --all --format="%ae%n%ce" | sort -u` → only `alexguimenti@gmail.com`
- `git log --all -p | grep -cE "sk-or-v1-[A-Za-z0-9]{20}|$ACCOUNT"` → `0`
- `git branch` → the same three branches; `git log --oneline | wc -l` → the same count as before the rewrite

- [ ] **Step 5: The next commits use the right e-mail**

Run: `git config user.email alexguimenti@gmail.com` (this repository only), then `git config user.email`.
Expected: `alexguimenti@gmail.com`.

- [ ] **Step 6: The suite once more on the rewritten history**

Run: `cd backend; uv run pytest tests/test_repo_layout.py -q`.
Expected: all passed. Then move this plan to `docs/plans/PLAN_REORG.md` (`git mv`), fix its row in `docs/README.md`, and commit: `git commit -m "docs: the reorganization is done; the plan moves to docs/plans"`.

---

## Phase 2 (optional, not part of this plan)

Each is its own plan if the owner wants it. They change paths the build, the tests or the deploy read, so each ends with `npm run check` and an `agentcore deploy`.

- **Medium:**
  - one `infra/` folder for what deploys (`agentcore/`, `runtime/`, `scripts/prepare_runtime.py`, the Lambda build in `scripts/build.mjs`);
  - `agent.toml` next to the agent (`backend/agent/agent.toml`), with the settings reading it from there;
  - `COMMANDS.md` into `docs/`;
  - `PROCESS.md` and `CODE_STANDARDS.md` merged into one guide.
- **Deep:**
  - `backend/app` (the API) and `backend/agent` (the agents) as two packages with their own dependencies, so the Copilot's runtime package stops carrying the app's;
  - the largest modules split by responsibility, with the duplicated HTTP helpers in `backend/agent/api.py` and `backend/agent/copilot/app_api.py` made one;
  - a template folder distilled from the project (an AgentCore app, a hand-written graph with a confirmation, the deploy) to start the next project from.
- **Publishing:**
  - a GitHub remote, a short licence note on the course material, and screenshots for the README.
