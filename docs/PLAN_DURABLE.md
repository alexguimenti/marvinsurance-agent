# Approvals that wait, and work that survives a restart: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Tick them off here.

**Goal:** The claim agent waits for a handler's sign-off and pays afterwards, carries on after a restart, never decides or pays twice, keeps its records on Supabase and is traced in Langfuse.

**Architecture:** The agent moves to its own package, `backend/agent/`, and its own web service on port 8002. The claim agent becomes a hand-written LangGraph graph with a checkpointer. Records go through SQLAlchemy Core operations in `backend/app/`, which the API and the agent both call in Python; each process has its own connections to the same database. The dashboard's calls still reach the backend, which forwards runs and sign-offs to the service.

**Tech Stack:** FastAPI, SQLAlchemy Core, SQLite (tests, e2e) and Supabase Postgres (`psycopg`), LangGraph with `PostgresSaver` and `SqliteSaver`, LangChain, OpenRouter, Langfuse, React, Vitest, Playwright.

**Spec:** `docs/DURABLE.md`. Read it alongside this plan. Where they disagree, the spec wins unless a line under "Decisions" says otherwise.

**Sidenote from the brief:** For consistency with the course, please read `labs/week2day3.ipynb` and build the solution in the same style as the lab. Where the lab and this brief differ, follow the brief. In this repo the lab is `labs/week2day3_mex.ipynb`.

## Decisions

The brief was written against a slightly different repo. The project owner settled the first three; the rest follow from the code.

| The brief says | This repo | Decision |
|---|---|---|
| Follow `docs/PROCESS.md`, `docs/CODE_STANDARDS.md`; update `docs/STATUS.md` | None of them exist, nor ever did in git | Owner: we write all three, describing this project (Task 12). The standards come from what the repo already does, not from the course's document, which we have not seen |
| Two sentences look cut: "payout rules. . Make them" and "in Langfuse, . Everything" | `app/payout.py` has the same calculation as the lab | Owner: nothing is missing. Keep the rules, add the limit, trace as the labs do (`CallbackHandler`, session id) |
| "The backend" answers the agent | Python calls on a shared database | Owner: the agent imports `app` operations and has its own connections. `uv run -m agent` works with the API down |
| `pay_claim` tool | `request_payment` | That is the tool the agent loses |
| "The middleware" | There is none: `run_graph` streams and traces | The graph's nodes take over `run_graph`'s job for the claim agent. The queue agent keeps `run_graph` |
| `npm run dev`, `uv run -m agent` | Neither exists at the root | Task 11 adds a root `npm run dev`; Task 9 adds `backend/agent/__main__.py`, run from `backend/` |
| API rules in `TECH_STACK.md` | Not written there, and `POST /runs` has optional inputs | Task 9 applies them to every route and writes them down. `POST /runs` splits in two |
| "Working the queue" section of `SPEC.md` | No such heading | Update sections 5 and 9. Sections 3.3 and 4 are the Rules: not touched |
| "UnderCover" | Marvin FartInsurance, the Farting Agent | Keep our names. `undercover` (schema) and `UNDERCOVER_AGENT` stay as the brief spells them, because IT chose them |
| At most 30 model calls | `max_steps = 40` graph steps | New setting `max_model_calls = 30`, replacing `max_steps`. `run_max_steps` stays for the queue agent |

The owner also confirmed these, each against a stated alternative:

1. Only the service works claims. The backend forwards a run to it and answers 503 when it is down (Task 9).
2. When it starts, the service only pays claims already signed off. Stopped claims and runs wait for the next run (Task 9).
3. A new request first finishes a stopped run with that run's cap and scope, and the new cap is dropped (Task 8).
4. Each attempt has its own thread and Langfuse session; a tag with the claim id finds them all (Tasks 2, 7).
5. An error after the decision is recorded is not a failed attempt: the claim keeps its thread and is carried on (Task 7).
6. `POST /runs` splits in two, and the API rules apply to every route, checked by a test (Task 9).
7. A run counts as dead after 45 s without a heartbeat, written every 15 s (Task 8).
8. `farting.db` is not migrated: Supabase starts from a reseed (Tasks 3, 4, 12).
9. A step repeated after a kill may appear twice in the trace. Records never repeat (Task 6).

Checked on LangGraph 1.2.18 with a throwaway graph, no model, on 2026-11-04. A `while` around `interrupt()` pauses again on an early resume. A resume after the sign-off pays once and reruns nothing before the paused node. `invoke(None)` after a `BaseException` or an `Exception` reruns only the node that was cut short. A resume on a finished thread does nothing. Not checked: the same on `PostgresSaver`, which Task 13 exercises.

## Global Constraints

- The limit: "The system still blocks any payout over the limit without a sign-off, whatever the agent does." The graph "never applies the limit itself".
- "A handler can only sign off for now"; no rejecting.
- `durability="sync"`; the synchronous checkpointer only; no async Postgres connections.
- Service address: `UNDERCOVER_AGENT` in the environment, or `http://127.0.0.1:8002`. E2e stand-in: port 8003.
- Supabase only when `DATABASE_URL` is in the process environment. A `.env` file alone must not switch a test to Supabase.
- All tables, the checkpointer's included, live in schema `undercover`, set on the connection. "at most 4 connections" per process, counting every pool: backend 4; agent 2 for records plus 2 for checkpoints.
- Every API operation has an `operation_id`, a one-line docstring and no optional inputs.
- Tests never call a model. `npm run check` is green with `.env` moved away. `data/evals/` and `data/security/` stay out of the app, the prompts and the tests.
- Any live run: reseed first, cap at five claims.
- Everything `npm run dev` starts must work on Windows.
- Row level security on every table in `undercover`, as the lab does: checkpoints hold each claim's full text.

## Review Focus

1. The agent service is down at sign-off: the sign-off stands, the claim stays in progress, and the service pays it when it next starts. Tests in Tasks 5, 8, 9.
2. An error after the decision was recorded (the payment step's database call fails): the claim keeps its thread and is carried on, it is not a failed attempt and does not start afresh. Test in Task 7.
3. Resume arrives twice, or before any sign-off: one payment for two; the graph stays paused for the early one. Tests in Tasks 6, 8.
4. Reseed while a claim is paused: the claim starts from nothing, and no old checkpoint revives it. Test in Task 4.
5. A second run requested while a run is genuinely alive in another process: refused, not adopted. A dead one: adopted. Test in Task 8.

---

## File structure

```
backend/app/       the records: db.py (tables, engine), claims.py (decisions, sign-off status), payments.py,
                   payout.py, runs.py, seed.py, reference.py, documents.py, config.py,
                   api.py (HTTP), agent_client.py (backend -> service)
backend/agent/     prompts.py, instructions_*.md   what the agent is told
                   tools.py                        what it can do
                   graph.py, worker.py, queue.py   how it works a claim and a queue
                   models.py                       chat_model, shared by the graph and the queue agent
                   service.py, __main__.py         the fourth part: its entry points
                   checkpoints.py, tracing.py, scripted.py
e2e/stand_in_agent.py    the service's stand-in on port 8003
scripts/dev.mjs          npm run dev
```

`app` never imports `langgraph`. Its one import from `agent` is `agent.prompts`, for `/instructions`, and `prompts.py` imports nothing heavy.

---

### Task 1: The payout operation

**Files:** Modify `backend/app/payout.py`, `backend/app/tools.py`, `backend/app/api.py`, `backend/app/instructions_claim.md`. Test `backend/tests/test_payout.py`, `test_api.py`.

**Interfaces:**
- Produces: `calculate_payout(items: list[LineItem], third_party_paid: float, deductible: float, benefit_max: float | None, approval_limit: float) -> dict` returning `payout`, `steps`, `approval_limit`, `needs_sign_off`. `POST /payouts/calculate`, `operation_id="calculate_payout"`.

- [x] **Step 1: Write the failing tests**

```python
def test_payout_says_whether_it_needs_a_sign_off():
    over = calculate_payout([LineItem(description="a", amount=1500, item_limit=None)], 0, 0, None, approval_limit=1000)
    assert over["payout"] == 1500 and over["needs_sign_off"] is True and over["approval_limit"] == 1000

def test_payout_at_the_limit_needs_no_sign_off():
    assert calculate_payout([LineItem(description="a", amount=1000, item_limit=None)], 0, 0, None, 1000)["needs_sign_off"] is False

def test_api_payout_takes_the_limit_from_company_json(client):
    body = {"items": [{"description": "a", "amount": 1200, "item_limit": None}], "third_party_paid": 0, "deductible": 200, "benefit_max": None}
    assert client.post("/payouts/calculate", json=body).json()["needs_sign_off"] is False
    del body["deductible"]
    assert client.post("/payouts/calculate", json=body).status_code == 422
```

- [x] **Step 2: Run them.** `cd backend; .venv/Scripts/python -m pytest tests/test_payout.py tests/test_api.py -q`. Expected: FAIL.
- [x] **Step 3: Implement.** Remove the defaults so every input is required, here and on the `calculate_payout` tool. `LineItem.item_limit` becomes required but nullable. The tool and the route pass `ref.company["payout_approval_limit"]`. Tell the model in `instructions_claim.md` to always pass all inputs and to ignore `needs_sign_off` when deciding.
- [x] **Step 4: Run all backend tests.** Expected: PASS.
- [x] **Step 5: Commit** `feat(payout): the payout operation takes the limit from company settings`.

### Task 2: Tracing in Langfuse

**Files:** Create `backend/agent/__init__.py`, `backend/agent/tracing.py`. Modify `backend/pyproject.toml` (add `langfuse`; `packages = ["app", "agent"]`; fix the stale `package-data`), `backend/app/agent.py`, `backend/app/config.py`. Test `backend/tests/test_tracing.py`.

**Interfaces:**
- Produces: `session_name(claim_id: str, thread_id: str) -> str` = `"CLM-1003:CLM-1003-1"`. `trace_config(claim_id: str, thread_id: str, model: str, handler_factory=...) -> dict` returning `callbacks` and `metadata` (`langfuse_session_id`, `langfuse_trace_name` = `"claim CLM-1003"`, `langfuse_tags` = `[claim_id, model]`). `flush_traces() -> None`.
- `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` are read from `os.environ` only (as the labs name them). Without them `trace_config` returns only `metadata` and `flush_traces` does nothing.

- [x] **Step 1: Write the failing tests**: `test_no_keys_no_callbacks`, `test_keys_add_the_handler` (fake `handler_factory`, `monkeypatch.setenv`), `test_session_named_after_claim_and_thread`, `test_keys_in_dot_env_alone_do_nothing`, `test_a_failing_handler_factory_never_fails_the_claim` (returns config without callbacks).
- [x] **Step 2: Run them.** Expected: FAIL, module missing.
- [x] **Step 3: Implement** using `langfuse.langchain.CallbackHandler` and the `Langfuse` client as in `labs/week2day2_mex.ipynb`. Import langfuse lazily so tests never load a client. After installing, confirm `from langfuse.langchain import CallbackHandler` still works: the dry run resolves langfuse 5.1.0, and the labs may have used another version. Add a `thread_id` argument to `Agent.decide`, which the runner passes as `f"{claim_id}-{attempts + 1}"`, and flush afterwards. Task 7 moves this into the worker.
- [x] **Step 4: Run all backend tests.** Expected: PASS.
- [x] **Step 5: Commit** `feat(tracing): trace each claim in a Langfuse session named after the claim and its thread`.

### Task 3: SQLAlchemy Core, still on SQLite

No behavior changes. This is the largest mechanical task; every later one builds on it.

**Files:** Rewrite `backend/app/db.py`. Modify every user of `sqlite3`: `api.py`, `runner.py`, `runs.py`, `payments.py`, `seed.py`, `tools.py`, `agent.py`, `orchestrator.py`, `scripted.py`, `scripts/reset_db.py`, `scripts/score.py`, and their tests. Add `sqlalchemy` to `pyproject.toml`.

**Interfaces:**
- Produces in `app/db.py`: `metadata`, one `Table` per current table (`claims`, `decisions`, `trace_events`, `payments`, `runs`, `todo_items`, `run_events`); `make_engine(settings: Settings, max_connections: int) -> Engine`; `init_schema(engine) -> None`; `reset_analyses(engine) -> None`; `insert_or_ignore(engine_or_conn, table) -> Insert` (picks the dialect's `on_conflict_do_nothing`, so a query is still written once).
- Every operation takes `engine: Engine` and opens a short `with engine.begin()`. No connection is held across a model call: with 2 connections for records that would stall the agent.
- Timestamps become `DateTime(timezone=True)` with `server_default=func.now()`, returned as ISO strings by the API. Primary keys autoincrement through `Integer`/`Identity`, not raw DDL.
- API timestamps go through one helper that treats SQLite's naive values as UTC, so both databases give the screens the same format. New ids come from `.returning()`, because `lastrowid` does not exist on Postgres.
- A partial unique index on `runs` where `status = 'running'` (`sqlite_where` and `postgresql_where`) makes "one run at a time" hold across processes.
- `init_schema` is idempotent and safe when two processes start together: it retries on `DBAPIError`, because each process's `create_all` can lose the race to create a table. The backend, the service and `uv run -m agent` each call it and seed at startup.
- `migrate()` and `ADDED_COLUMNS` go. `create_all` plus a reseed replaces them. An old `farting.db` is not upgraded: Task 11 documents deleting it.

- [x] **Step 1: Write the failing tests** in `test_db.py`: `test_tables_exist_after_init`, `test_insert_or_ignore_keeps_the_first_row`, `test_make_engine_caps_connections` (`engine.pool.size() == 4`, `_max_overflow == 0`), `test_reset_leaves_every_claim_waiting`, `test_two_processes_can_init_together` (threads on one SQLite file), `test_only_one_run_can_be_running`. Delete the migration tests.
- [x] **Step 2: Run them.** Expected: FAIL.
- [x] **Step 3: Implement `db.py`, then port each module with its tests, one at a time**: seed, payments, runs, runner, tools, agent, orchestrator, api. Turn on `PRAGMA journal_mode=WAL` and a 10 s timeout in a `connect` event. Rows become `.mappings()` results.
- [x] **Step 4: Check nothing is left.** `grep -rn "sqlite3" backend/app scripts` returns nothing. Expected: empty.
- [x] **Step 5: Run `npm run check`.** Expected: all green, same test counts apart from the replaced migration tests.
- [x] **Step 6: Commit** `refactor(db): SQLAlchemy Core, one query for SQLite and Postgres`.

### Task 4: Supabase and reseeding

**Files:** Modify `backend/app/config.py`, `backend/app/db.py`, `scripts/reset_db.py`, root `package.json`, `backend/tests/conftest.py`, `scripts/check.mjs`. Add `psycopg[binary,pool]`, `python-dotenv`, `httpx` (moved from dev) to `pyproject.toml`. Test `test_config.py`, `test_db.py`.

**Interfaces:**
- Produces: `Settings.database_url: str | None`, taken from `os.environ` after the sources load, never from `.env`. `make_engine` normalizes `postgresql://` to `postgresql+psycopg://`, sets `pool_pre_ping=True`, `pool_recycle=300`, and on `connect` runs `SET search_path TO undercover` (switching autocommit around it, as SQLAlchemy's docs show). `init_schema` on Postgres also runs `CREATE SCHEMA IF NOT EXISTS undercover` and `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` on each table.
- `scripts/reset_db.py`: creates the tables, clears the analyses and seeds. From Task 7 on it also clears the checkpoints (`agent.checkpoints.clear_checkpoints(settings)`). `npm run reseed` loads `.env` and runs it, printing the claim count and the database's host, never its password.
- `UNDERCOVER_AGENT` follows the same rule as `DATABASE_URL`: from the process environment only.
- `conftest.py` deletes `DATABASE_URL`, `LANGFUSE_*` and `UNDERCOVER_AGENT` from the environment for every test. `check.mjs` deletes them before spawning.

- [x] **Step 1: Write the failing tests**

```python
def test_database_url_in_dot_env_alone_is_ignored(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://nope/nope")
    monkeypatch.chdir(tmp_path)
    assert Settings(_env_file=tmp_path / ".env").database_url is None

def test_database_url_in_the_environment_is_used(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/d")
    assert Settings().database_url == "postgresql://u:p@h/d"

def test_postgres_url_uses_psycopg_and_the_connection_cap(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/d")
    engine = make_engine(Settings(), max_connections=2)  # creating an engine opens no connection
    assert engine.dialect.driver == "psycopg" and engine.pool.size() == 2
```

Review Focus 4 needs the checkpointer, so its test is in Task 7. Until then `reset_db.py` leaves the `clear_checkpoints` call out.

- [x] **Step 2: Run them.** Expected: FAIL.
- [x] **Step 3: Implement.** Then, with `.env` moved aside, run the backend tests.
- [x] **Step 4: Run `npm run check`.** Expected: green.
- [x] **Step 5: Commit** `feat(db): Supabase when DATABASE_URL is in the environment; schema undercover; reseed`.

### Task 5: Operations that happen once, and a sign-off that does not pay

**Files:** Create `backend/app/claims.py`. Rewrite `backend/app/payments.py`. Modify `backend/app/db.py` (columns), `backend/app/api.py`. Test `backend/tests/test_claims.py`, `test_payments.py`, `test_api.py`.

**Interfaces:**
- Payment statuses: `held` -> `signed_off` -> `paid`; or `paid` straight away within the limit. New columns `payments.approved_at`, `payments.paid_at`; `approved_by` stays. New column `claims.thread_id`.
- Produces in `app/claims.py`: `record_decision(engine, ref, claim_id, decision: dict, model: str) -> dict`. It returns the claim. The same decision and payout again counts as done and returns the claim; a different one raises `DecisionConflict`. `needs_sign_off(engine, ref, claim_id) -> bool` is true for an approval over the limit with no sign-off. `hold_for_sign_off(engine, ref, claim_id) -> dict` creates the held payment once and sets `awaiting_approval`.
- Produces in `app/payments.py`: `sign_off(engine, ref, payment_id, handler_id) -> Payment` sets `signed_off`, `approved_by`, `approved_at` and moves the claim to `in_progress`, in one transaction; a repeat changes nothing. `pay(engine, ref, claim_id) -> dict` takes the amount from the recorded decision, never from the caller; a repeat returns the claim; over the limit with no sign-off raises `SignOffRequired`; it sets `decided` and `paid_at`.
- `record_decision` also writes the manual and wording versions as `runner.py` does today.

- [x] **Step 1: Write the failing tests**: `test_repeating_a_decision_counts_as_done`, `test_a_different_decision_is_refused`, `test_needs_sign_off_is_the_backends_call` (limit, at the limit, decline), `test_pay_within_limit`, `test_pay_over_limit_without_sign_off_is_refused`, `test_paying_twice_pays_once`, `test_pay_amount_comes_from_the_decision`, `test_sign_off_does_not_pay` (`status == "signed_off"`, `paid_at is None`, claim `in_progress`, `approved_by`/`approved_at` set), `test_sign_off_twice_keeps_the_first_handler`, `test_handler_without_the_right_is_refused` (kept), `test_api_approve_returns_the_signed_off_payment`.
- [x] **Step 2: Run them.** Expected: FAIL.
- [x] **Step 3: Implement.** `runner.py` calls `record_decision`, `hold_for_sign_off` and `pay` for now, so the suite stays meaningful until Task 7; `_check_payment` goes. The old `request_payment` function stays only for the tool of that name, until Task 6 removes both. `/approvals` keeps listing `held`.
- [x] **Step 4: Run all backend tests.** Expected: PASS. Update those that expected the sign-off to pay.
- [x] **Step 5: Commit** `feat(payments): repeats count as done; a sign-off records who and when, and does not pay`.

### Task 6: The claim graph

**Files:** Create `backend/agent/graph.py`, `backend/agent/prompts.py`. Move with `git mv`: `app/tools.py` -> `agent/tools.py`, `app/scripted.py` -> `agent/scripted.py`, `app/instructions_*.md` -> `agent/`. Modify `backend/app/agent.py` (delete `LangChainAgent`; `run_graph` and `chat_model` stay for the queue agent until Task 8). Test `backend/tests/test_graph.py`; port `test_agent.py`, `test_tools.py`, `test_evals_isolation.py`; extend `tests/fakes.py`.

**Interfaces:**
- Produces: `class ClaimState(BaseModel)` with `claim_id`, `messages` (`add_messages`), `proposal: dict | None`, `decision: dict | None`, `model_calls: int`, `usage: dict`. Dicts only, so checkpoints need no serializer allowlist.
- `build_claim_graph(engine, ref, model, model_id: str, max_model_calls: int, checkpointer) -> CompiledStateGraph`.
- Nodes: `prepare` (the prefetched messages of today, in the state) -> `model` -> (`tools` -> `model`, or `decide` when the proposal is saved) -> `decide` calls `record_decision` -> decline or refer end; approve goes to `sign_off` -> `pay` -> end.
- `sign_off`: `hold_for_sign_off` when `needs_sign_off`, then `while needs_sign_off(...): interrupt({...})`, so an early resume pauses again.
- Tools: `save_decision(decision, payout, calculation, rationale, citations)`, in the lab's style with `InjectedState` and `Command`, refusals as `Error: ...` text. It also applies the lab's checks, including payout at most the amount claimed. `ToolNode` returns tool exceptions to the model. `parallel_tool_calls=False`. `request_payment` and `list_sections` are gone from what the model is offered; keep the section cap and no-reread rules.
- `decide` also records today's `rationale_warnings` as `warning` events, in the same order as today.
- `Decision` moves to `app/claims.py`, `chat_model` to `agent/models.py`.
- Tracing: `model`, `tools`, `decide`, `sign_off` and `pay` append to `trace_events` through `app.runs.add_trace_event(engine, claim_id, kind, content)`, in today's kinds and summaries. A step repeated after a kill can appear twice in the trace; the records cannot. `usage` is written in `decide`, and by the worker on a failure.
- `model` raises `TooManyModelCalls` before the 31st call. `recursion_limit` is `2 * max_model_calls + 10`. A deadline in `config["configurable"]` keeps `claim_timeout_s`.
- `agent.scripted.ScriptedChatModel`: replaces `ScriptedAgent` with the same claims' behavior (CLM-1013 always fails, CLM-1017 fails on the first thread, CLM-1003/1008/1011 approve over the limit), driving the real graph.
- `instructions_claim.md`: no payment section; finish with `save_decision`; the system pays.

- [x] **Step 1: Write the failing tests on a stub model and `InMemorySaver`**

```python
def test_decline_ends_without_a_payment(graph_env): ...
def test_approval_within_limit_is_paid_straight_after_the_decision(graph_env): ...
def test_approval_over_limit_pauses_before_paying(graph_env):
    result = graph_env.invoke("CLM-1003", approve=3750)
    assert result["__interrupt__"]
    assert graph_env.claim("CLM-1003")["status"] == "awaiting_approval"
    assert graph_env.payment("CLM-1003")["status"] == "held"

def test_resume_before_a_sign_off_stays_paused(graph_env): ...   # Review Focus 3
def test_resume_after_sign_off_pays_once_without_calling_the_model(graph_env): ...
def test_refused_tool_call_goes_back_to_the_model(graph_env): ...
def test_every_step_is_recorded_on_the_claim(graph_env): ...
def test_thirty_model_calls_is_the_most(graph_env):
    with pytest.raises(TooManyModelCalls):
        graph_env.invoke("CLM-1001", tool_loop=True)
    assert graph_env.model.calls == 30

def test_model_is_not_offered_a_payment_tool(graph_env): ...
```

- [x] **Step 2: Run them.** Expected: FAIL, module missing.
- [x] **Step 3: Implement**, then port `test_agent.py` and `test_tools.py` (prefetch, section index, prefetched facts traced). `test_evals_isolation.py` must scan `backend/agent/` as well as `backend/app/`, and read the instructions from their new place: moving them must not take them out of the scan.
- [x] **Step 4: Run** `tests/test_graph.py tests/test_tools.py tests/test_evals_isolation.py`. Expected: PASS. `test_runner`, `test_orchestrator` and `test_api` may fail here; Task 7 restores them before the commit.
- [x] **Step 5: Hold the commit until Task 7 Step 4** if they do, so no commit leaves the suite red.

### Task 7: Working a claim: first time, carrying on, starting afresh

**Files:** Create `backend/agent/worker.py` (from `app/runner.py`, deleted), `backend/agent/checkpoints.py`. Modify `backend/app/config.py`, `backend/app/runs.py`, `scripts/score.py`, `scripts/reset_db.py`, `agent.toml`, `backend/pyproject.toml` (`langgraph`, `langgraph-checkpoint-postgres`, `langgraph-checkpoint-sqlite`). Test `backend/tests/test_worker.py` (from `test_runner.py`).

**Interfaces:**
- `checkpoints.make_checkpointer(settings) -> context manager yielding a saver`. Postgres: `ConnectionPool(max_size=2, check=ConnectionPool.check_connection, kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0}, configure=<SET search_path TO undercover>)` and `PostgresSaver`, as in the lab, then `setup()` and RLS on its tables. SQLite: `SqliteSaver` on `<stem>.checkpoints.db` beside the database, so a test or a local run survives a restart and `.gitignore` already covers the file. `clear_checkpoints(settings) -> None`.
- `worker.work_claim(env: AgentEnv, claim_id, run_id, max_attempts) -> str`: the one-line reports of today. Takes a waiting claim atomically, sets `thread_id = f"{claim_id}-{attempts + 1}"`, invokes with `durability="sync"`.
- `worker.carry_on(env, claim_id) -> str`: on the claim's stored thread, with attempts unchanged. `Command(resume={"signed_off": True})` when the state is at an interrupt; `graph.invoke(None, config)` otherwise; the first input when the thread has no checkpoint yet, because the process died before the first step was saved. It reports from the records when the thread already finished.
- After any invoke that ends paused, the worker checks `needs_sign_off` once more and carries on at once if it is false. A sign-off that lands while the graph is still pausing would otherwise wait for the next run.
- `runs.finish` no longer puts in-progress claims back in the queue: that would undo a sign-off and start a decided claim again. `runs.missed_claims` counts an in-progress claim as owed only when it has no decision, so a run can finish while a signed-off claim is being paid. A to-do item can be `done` once its claim has a decision.
- `scripts/score.py --run` works on the graph with `AGENT_PROVIDER`'s model, a temporary SQLite database and temporary checkpoints, whatever `DATABASE_URL` says. A claim that pauses still counts: its decision is recorded. Its `--split` without `--run` reads the configured database as it does today.
- `worker.stopped_claims(engine) -> list[str]` (in progress, with a thread). `worker.signed_off_claims(engine) -> list[str]`.
- Failure rule: an `Exception` before a decision exists fails the attempt as today (`attempts + 1`, new thread next time, `needs_handler` after `max_attempts`). After a decision exists, it leaves the claim in progress on its thread for carrying on, and the report says so. `KeyboardInterrupt` and kills leave everything as it is.
- `AgentEnv` dataclass: `engine, ref, settings, graph, model_id`.

- [x] **Step 1: Write the failing tests on a stub model and `SqliteSaver`.** Stop a claim by having the stub raise `class Stopped(BaseException)`; rebuild the graph on a new saver over the same file to stand in for a new process.

```python
def test_stopped_claim_carries_on_from_its_last_step(env):
    work_stopping_after(env, "CLM-1001", model_calls=2)
    assert claim(env, "CLM-1001")["status"] == "in_progress"
    thread = claim(env, "CLM-1001")["thread_id"]
    fresh = reopen(env)
    assert carry_on(fresh, "CLM-1001").startswith("CLM-1001: approve")
    row = claim(fresh, "CLM-1001")
    assert row["thread_id"] == thread and row["attempts"] == 0
    assert fresh.model.calls == remaining_calls  # the first two are not made again
    assert count(fresh, "decisions", "CLM-1001") == 1

def test_claim_that_errors_starts_afresh_on_a_new_thread(env): ...
def test_error_after_the_decision_is_carried_on_not_retried(env): ...   # Review Focus 2
def test_two_failures_leave_it_for_a_handler(env): ...
def test_repeated_decide_step_after_a_stop_counts_as_done(env): ...
def test_reseed_leaves_no_checkpoint_behind(env): ...   # Review Focus 4
def test_killed_before_the_first_checkpoint_starts_on_the_same_thread(env): ...
def test_sign_off_during_the_pause_is_not_left_waiting(env): ...
def test_finishing_a_run_leaves_a_signed_off_claim_in_progress(env): ...
def test_run_can_finish_while_a_signed_off_claim_is_being_paid(env): ...
def test_after_the_pause_the_trace_goes_in_the_same_session(env): ...
def test_score_never_uses_database_url(monkeypatch): ...
```

- [x] **Step 2: Run them.** Expected: FAIL.
- [x] **Step 3: Implement.** `agent.toml` and `Settings`: `max_model_calls = 30` in place of `max_steps`. Point `orchestrator.py` and `api.py` at the worker, still in-process, so the suite is whole.
- [x] **Step 4: Run all backend tests.** Expected: PASS.
- [x] **Step 5: Commit** (with Task 6) `feat(agent): hand-written claim graph with sign-off pause, checkpoints and carrying on`.

### Task 8: A stopped queue run is finished by the next one

**Files:** Move `app/orchestrator.py` -> `agent/queue.py`; shrink `app/agent.py` into `agent/queue.py` and `agent/prompts.py`, then delete it. Modify `backend/app/runs.py`, `app/db.py` (`runs.heartbeat_at`), `agent/instructions_run.md` (the new one-line answers: a claim carried on, an error left for the next run, and that an adopted run may already have work done; still no claim ids). Test `test_queue.py` (from `test_orchestrator.py`), `test_runs.py`.

**Interfaces:**
- `runs.touch(engine, run_id)`; `runs.live_run(engine, now) -> row | None`: running with a heartbeat within `RUN_STALE_S = 45`. `runs.stale_run(engine, now) -> row | None`. A heartbeat thread beats every 15 s during a run.
- `queue.begin_run(env, limit: int | None, claim_ids: list[str] | None) -> int`: refuses with `RunActive` when a live run exists; adopts a stale run if there is one, with its cap and scope (the new request's are dropped, and the summary says the run was finished); otherwise creates one. It returns at once, so the service can answer with the run id.
- `queue.work_run(env, run_id) -> None`: carries on every signed-off and stopped claim, then runs the queue agent. Claims a resume in this process is already working are skipped.
- `recover_after_restart` is deleted. Nothing marks a stopped run failed or puts its claims back any more.
- Carried-on claims do not count again towards a cap. `set_todo` on an adopted run keeps items already done. The queue agent stays on `create_agent`, and starts a fresh conversation over the same run row.

- [x] **Step 1: Write the failing tests**: `test_a_run_first_carries_on_stopped_and_signed_off_claims`, `test_stopped_run_is_finished_by_the_next_with_its_cap`, `test_a_live_run_in_another_process_is_refused`, `test_a_dead_run_is_adopted` (both with an injected `now`; Review Focus 5), `test_todo_keeps_done_items_on_an_adopted_run`, `test_backend_restart_changes_nothing` (replaces the recovery tests).
- [x] **Step 2: Run them.** Expected: FAIL.
- [x] **Step 3: Implement.**
- [x] **Step 4: Run all backend tests.** Expected: PASS.
- [x] **Step 5: Commit** `feat(queue): a run carries on stopped claims and finishes a stopped run`.

### Task 9: The agent's service, its command line, and the API rules

**Files:** Create `backend/agent/service.py`, `backend/agent/__main__.py`, `backend/app/agent_client.py`, `backend/uv.lock`. Modify `backend/app/api.py`, `backend/app/config.py`. Test `backend/tests/test_service.py`, `test_cli.py`, `test_api.py`.

**Interfaces:**
- Service (`create_service(settings) -> FastAPI`, run as `agent.service:app`), with `def` routes and an executor of 3 threads, one for a queue run and two for resumes: `GET /health` (`get_agent_health`), `POST /runs` with `{limit: int}` (`start_run`), `POST /runs/selected` with `{claim_ids: list[str]}` (`start_selected_run`), `POST /claims/{claim_id}/resume` (`resume_claim`, 202). A claim already being worked in this process, by a resume or by a run, is not submitted again; both paths share one set of claim ids. Bound to `127.0.0.1`.
- On startup the service resumes signed-off claims only. Stopped claims and stopped runs wait for the next run, so a restart of `npm run dev` never spends model money by itself.
- `Settings.agent_url` reads `UNDERCOVER_AGENT` or defaults to `http://127.0.0.1:8002`.
- `agent_client.notify_sign_off(url, claim_id) -> None`: `httpx` with a 2 s timeout, logs and swallows connection errors. `agent_client.start_run(url, path, body)`: maps the service's 409 to 409 and connection errors to 503 `"the agent is not running"`.
- Backend: `POST /runs` with `{limit: int}`, `POST /runs/selected` with `{claim_ids: [...]}` (404 for an unknown claim, 422 for empty) forward to the service. `POST /payments/{id}/approve` commits, then notifies in a `BackgroundTasks` task. Every route gets an `operation_id` and a one-line docstring; a test walks `app.routes` and asserts both, and that no request model has an optional field, for the backend and the service.
- A blank limit on the command line means every waiting claim, at least 1, when handed to the service.
- `uv run -m agent [--limit N] [--claims CLM-1,CLM-2]`: loads `<root>/.env` with `python-dotenv` at this entry only. If `/health` answers, it hands the run to the service and follows it; otherwise it runs in-process. Prints the run's summary. Ctrl-C leaves everything to carry on.
- The backend process keeps 4 connections; the agent's 2 + 2.

- [x] **Step 1: Write the failing tests**

```python
def test_sign_off_reaches_the_service_after_it_is_saved(client, fake_service): ...
def test_sign_off_stands_when_the_service_is_down(client):  # Review Focus 1
    response = approve(client, "PAY-CLM-1003", "HND-01")
    assert response.status_code == 200
    assert claim(client, "CLM-1003")["status"] == "in_progress"

def test_service_resume_pays_once_even_when_asked_twice(service_env): ...   # Review Focus 3
def test_service_start_pays_claims_signed_off_while_it_was_away(service_env): ...   # Review Focus 1
def test_service_start_leaves_stopped_claims_alone(service_env): ...
def test_run_with_no_agent_is_503(client): ...
def test_every_operation_follows_the_api_rules(): ...
def test_cli_runs_in_process_when_the_service_is_down(cli_env): ...
def test_cli_hands_over_when_the_service_is_up(cli_env): ...
```

- [x] **Step 2: Run them.** Expected: FAIL.
- [x] **Step 3: Implement.** Create `uv.lock` with `uv lock` from `backend/`. `grep -rn "langgraph\|langchain" backend/app` must return nothing.
- [x] **Step 4: Run all backend tests.** Expected: PASS.
- [x] **Step 5: Commit** `feat(agent): the agent's own service; a sign-off reaches it once saved`.

### Task 10: The dashboard

**Files:** Modify `frontend/src/api.ts`, `format.ts`, `pages/Queue.tsx`, `pages/ClaimDetail.tsx`, `pages/Approvals.tsx`, their tests.

**Interfaces:**
- `/claims/{id}` adds `payment.approved_by_name`, resolved from `data/handlers.json`, with a test in `test_api.py`. `payment.approved_at` and `payment.paid_at` already come from Task 5.
- `startRun` picks the route: `{limit}` or `{claimIds}`. A blank Limit sends the number of waiting claims, at least 1, so a stopped run can still be finished from an empty queue.

- [x] **Step 1: Write the failing tests**: `ClaimDetail` "shows who signed off and when on a paid claim"; "shows in progress, and no held notice, between the sign-off and the payment" (`status: in_progress`, `payment.status: signed_off`); `Queue` "says the agent is not running on a 503"; `Approvals` "a signed-off payment leaves the list"; `startRun` sends a limit when the field is blank.
- [x] **Step 2: Run them.** `cd frontend; npx vitest run`. Expected: FAIL.
- [x] **Step 3: Implement**, reusing the existing styles. `stampFor` keeps "Pending sign-off" until the payment is `paid`. `ClaimDetail` already polls every 2 s, within the five seconds asked for.
- [x] **Step 4: Run** `npx vitest run; npx tsc -b; npx oxlint --deny-warnings src`. Expected: PASS.
- [x] **Step 5: Commit** `feat(frontend): in progress after a sign-off; the payment shows who and when`.

### Task 11: `npm run dev` and the end-to-end tests

**Files:** Create `scripts/dev.mjs`, `e2e/stand_in_agent.py`. Modify root `package.json`, `e2e/playwright.config.ts`, `e2e/scripts/clean.mjs`, `e2e/tests/*.spec.ts`, `scripts/check.mjs`.

**Interfaces:**
- `npm run dev`: `process.loadEnvFile('.env')` when present, then starts the backend on 8000 (with `--reload --no-access-log`), the service on 8002 (no reload: a reload would stop a claim), and Vite on 5173. Output prefixed by part. Ctrl-C ends all three, with `taskkill /T /F` on Windows. If one dies, it stops the others and says which.
- `e2e/stand_in_agent.py` on port 8003: the real scripted service for runs, whose `/claims/{id}/resume` only records the id. `GET /received` returns the ids. The e2e backend gets `UNDERCOVER_AGENT=http://127.0.0.1:8003` and `AGENT_PROVIDER=scripted`. The backend and the stand-in share one throwaway database, `e2e/.tmp/e2e.db`, as the backend and the service do in production. `clean.mjs` also removes its checkpoints file.

- [x] **Step 1: Write the failing e2e test** in `02-approval.spec.ts`:

```ts
test('a sign-off on the dashboard reaches the agent service', async ({ page, request }) => {
  await page.goto('/approvals')
  const row = page.locator('tbody tr', { hasText: 'CLM-1003' })
  await page.getByLabel('Approve as').selectOption({ label: 'Dana Whitfield (Senior Claims Handler)' })
  await row.getByRole('button', { name: 'Approve payment' }).click()
  await expect.poll(async () => (await request.get('http://127.0.0.1:8003/received')).json()).toEqual(['CLM-1003'])
  await page.goto('/claims/CLM-1003')
  await expect(page.locator('.badge-in_progress')).toBeVisible()
})
```

- [x] **Step 2: Run it.** `cd e2e; npm test`. Expected: FAIL.
- [x] **Step 3: Implement.** Port the other specs to the new routes and to `in_progress` after a sign-off. A paid screen is covered by Task 10's test and the live run.
- [x] **Step 4: Move `.env` aside and run `npm run check`; put `.env` back.** Expected: all green with no network.
- [x] **Step 5: Start `npm run dev` on Windows**, with the owner's 8000 and 5173 servers stopped first, by the owner. Check all three answer, then Ctrl-C and confirm that no process is left on the three ports. Expected: all answer, none left.
- [x] **Step 6: Commit** `feat: npm run dev starts all three parts; e2e checks a sign-off reaches the service`.

### Task 12: The documents

**Files:** Create `docs/PROCESS.md`, `docs/CODE_STANDARDS.md`, `docs/STATUS.md`. Modify `docs/SPEC.md`, `docs/TECH_STACK.md`, `COMMANDS.md`, `README.md`.

- `PROCESS.md`: how we plan and tick off, TDD, `npm run check`, `npm run dev`, Windows, the reseed-and-cap rule for live runs.
- `CODE_STANDARDS.md`: rules the repo already follows, and the agent's parts: prompts, tools, the run loop (the graph's nodes in the claim agent), and the service as the fourth.
- `STATUS.md`: what works today and what comes later (rejecting a held payout).
- `SPEC.md`: sections 5 and 9, including the line that a server stopped mid-run marks the run failed. Sections 3.3 and 4 unchanged. `git diff` over those two must be empty.
- `TECH_STACK.md`: Supabase, SQLAlchemy, LangGraph, Langfuse, the service, the API rules, the new environment variables.
- `COMMANDS.md`: `npm run dev`, `npm run reseed`, `uv run -m agent`, and that an old `farting.db` should be deleted.

- [x] **Step 1: Write them**, then check each command in `COMMANDS.md` by running it, except the live ones.
- [x] **Step 2: Commit** `docs: describe the durable agent; add process, standards and status`.

### Task 13: Live verification

Needs the owner. This task spends money on OpenRouter and writes to Supabase and Langfuse. Do Step 1 before starting.

**Files:** Create `scripts/check_langfuse.py` (prints a session's trace count and times, no claim text). Move `backend/.mcp.json` to the root `.mcp.json`. Record results in `docs/STATUS.md`.

- [x] **Step 1: Prepare with the owner.** The Supabase MCP server is configured in `backend/`, so a session opened at the root does not load it. After the move, the owner restarts the session and authenticates with `/mcp`. The owner stops any old servers on 8000 and 5173 and closes the lab notebooks: the lab's checkpoint pool alone holds up to 5 connections, and they count towards the 12. Take a baseline of `pg_stat_activity` for user postgres before starting anything.
- [x] **Step 2: Reseed.** `npm run reseed`. Expected: 25 claims. Through the MCP server, the tables are in schema `undercover` with RLS on.
- [x] **Step 3: Live run.** `npm run dev`, then a run capped at five on the default model. Expected: five claims worked. During the run, `pg_stat_activity` for user postgres shows no more than 12.
- [x] **Step 4: Sign off through the Playwright MCP server.** Expected: the over-limit claim waits, then shows as paid within five seconds, with the handler and the time. If the run holds no claim, because the live model decided otherwise, reseed and run again with `--claims` naming five claims that include CLM-1003, CLM-1008 and CLM-1011, which the scripted agent treats as over the limit.
- [x] **Step 5: Langfuse.** `scripts/check_langfuse.py <session>`. Expected: traces from before and after the sign-off in the held claim's session.
- [x] **Step 6: Kill test.** Reseed. `uv run -m agent --limit 5` with the service stopped; after a claim's first steps, kill that process. Run again after 45 s. Expected: same `thread_id`, same `attempts`, one decision.
- [x] **Step 7: Commit** `docs: record the live verification`.

Found in this task: a sign-off first took 21 s to show as paid, because a query cost four trips of 220 ms and a new connection 6.5 s. Statements now commit by themselves, connections open at startup, and waiting and paying are one step of the graph (the plan had `sign_off` and `pay` as two).

## Done means

Each line under "What done looks like" in `docs/DURABLE.md` has its evidence: tests in Tasks 1 to 11, measurements in Task 13, documents in Task 12.

## Results

See `docs/STATUS.md`. One line of "What done looks like" is not met as written: the peak of connections for user postgres was 15, not 12, with a lab notebook's four among them, and it was read with SQL because the Supabase MCP server was not loaded in that session.
