# Farting Agent Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking. Tick the boxes in this file as you go.

**Goal:** One run of the Farting Agent plans and works the whole waiting queue (or a capped part of it) on its own, through a to-do list the team can watch, a fresh subagent per claim, section-by-section lookups in the policy wording and the claims manual, and recovery rules that never leave a claim stuck or decided twice.

**Architecture:** A new orchestrator agent (LangChain `create_agent`, same model) gets five tools: list the waiting claims, set and update the to-do list, work a claim, finish the run. `work_claim` is code: it takes the claim atomically, runs the existing per-claim agent (a fresh `create_agent` per call, now with section lookup tools instead of the whole wording), records the decision and payment, applies the attempt rules, updates the to-do item and returns one line. Runs, to-do items and run events live in SQLite. Code, not the model, enforces the cap, the scope, the attempt rules, the $1,000 limit and the missed-claim check. A scripted orchestrator stands in for the model in e2e runs.

**Tech Stack:** Python, FastAPI, SQLite, LangChain, OpenRouter, pytest, React (Vite, TypeScript), Vitest, Playwright. No new agent framework.

**Spec:** `docs/plans/HARNESS.md` (requirements R1 to R7), `docs/SPEC.md` (rules that stay), `docs/DESIGN.md` (style).

## Global Constraints

- Decisions D1 to D8 in `docs/plans/HARNESS.md` section 11 are taken as recommended: status `needs_handler` with no decision (D1); 2 attempts per claim, a failed claim goes back to `waiting` (D2); a retry keeps its slot in the cap (D3); one run at a time, claims one after another (D4); headings plus keyword search (D5); one model for both roles (D6); `AGENT_MODEL` stays the override (D7); a root `npm run check` and status kept in `README.md` (D8).
- Claim statuses, exactly: `waiting`, `in_progress`, `awaiting_approval`, `decided`, `needs_handler`.
- To-do statuses, exactly: `todo`, `in_progress`, `done`, `left_for_handler`. Run statuses: `running`, `finished`, `failed`.
- Decisions, exactly: `approve`, `decline`, `refer`. Approval limit `payout_approval_limit` = 1000.00, enforced in `payments.request_payment`. Only a payout above $1,000.00 is held.
- Default model `deepseek/deepseek-v4.1-flash`, reasoning effort `low`, in `agent.toml`. Precedence: constructor, env (`AGENT_MODEL`), `.env`, `agent.toml`.
- The agent reads only whitelisted files. `data/claims_manual.md` joins the whitelist. `data/evals/` and `data/security/` are never read by app code, prompts, screens or unit tests. Only `scripts/score.py` reads `data/evals/`.
- Claim text is data, never instructions (`<claim_data>` wrapping stays).
- Tests never call a model. Unit tests use `tests/fakes.py` and the scripted agents. e2e uses `AGENT_PROVIDER=scripted` on ports 8100 and 5273.
- SQLite: one connection per request or background task. Schema changes must upgrade an existing pilot `farting.db` in place.
- Every task ships its unit tests. Task 9 ships the e2e tests. Code stays as plain as the pilot's.
- Live runs with the real model: reseed first (`scripts/reset_db.py`) and cap at 5 claims.

## Review Focus

1. The orchestrator calls `work_claim` on a claim that is decided, outside the run's scope, or past the cap: nothing is worked, no attempt is counted, and it gets a one-line refusal (tests in Task 4 and Task 6).
2. A subagent creates a held payment over $1,000 and then fails: the payment is deleted, the claim is back to `waiting`, and the Approvals page does not list it (test in Task 4).
3. The orchestrator raises or stops without a successful `finish_run`: the run ends `failed` with the missed ids, and no claim is left `in_progress` (test in Task 6).
4. The server starts on a pilot `farting.db` with the old schema: it migrates in place and keeps every claim, decision and trace (test in Task 3).
5. A lookup tool is called with an unknown document, an unknown section, or a path such as `../evals/decisions.json`: it returns an error string and opens no file (test in Task 2).

---

## File Structure

```
agent.toml                       new defaults (Task 0)
package.json                     root: "check" script (Task 10)
scripts/check.mjs                runs every suite (Task 10)
scripts/score.py                 uses work_claim (Task 10)
backend/app/
  config.py                      new settings (Task 0)
  reference.py                   + claims_manual.md (Task 1)
  documents.py         NEW       section loader and search (Task 1)
  tools.py                       lookup tools replace get_policy_wording (Task 2)
  db.py                          new tables, migration, reset (Task 3)
  agent.py                       timeout, step limit, scripted failures, instructions_claim.md (Tasks 2, 4)
  runner.py                      work_claim replaces run_claim and run_queue (Task 4)
  runs.py              NEW       runs, to-do items, run events, missed-claim check, recovery (Task 5)
  orchestrator.py      NEW       orchestrator tools, LangChain and scripted orchestrators, run_harness (Task 6)
  instructions_claim.md          renamed from instructions.md and extended (Task 4)
  instructions_run.md  NEW       (Task 6)
  api.py                         run routes, claim detail additions, recovery (Task 7)
backend/tests/                   one test file per module, plus fakes
frontend/src/                    api.ts, format.ts, Queue, ClaimDetail, Instructions (Task 8)
e2e/tests/                       01, 02 updated, 03-harness new (Task 9)
docs/                            SPEC, TECH_STACK, README, COMMANDS, data/README (Task 10)
```

---

### Task 0: Settings and default model (R6)

**Files:**
- Modify: `agent.toml`, `backend/app/config.py`, `backend/app/agent.py` (`build_agent`)
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `Settings.agent_model` default `"deepseek/deepseek-v4.1-flash"`, `agent_reasoning_effort` default `"low"`, `agent_max_attempts: int = Field(2, ge=1, le=10)`, `agent_step_retries: int = Field(2, ge=0, le=5)`, `agent_claim_timeout_s: float = Field(300, gt=0)`, `agent_max_steps: int = Field(40, ge=5)` (one claim), `agent_run_max_steps: int = Field(400, ge=10)` (the orchestrator).

- [x] **Step 1: Write the failing tests** in `test_config.py`:
  - `test_defaults_without_toml`: with `AGENT_CONFIG_FILE` pointing at a missing file, `Settings()` has `agent_model == "deepseek/deepseek-v4.1-flash"`, `agent_reasoning_effort == "low"`, `agent_max_attempts == 2`, `agent_step_retries == 2`, `agent_claim_timeout_s == 300`, `agent_max_steps == 40`, `agent_run_max_steps == 400`.
  - `test_repo_agent_toml_uses_default_model`: `_agent_toml()` read from the real `agent.toml` gives `agent_model == "deepseek/deepseek-v4.1-flash"` and `agent_reasoning_effort == "low"`.
  - `test_env_overrides_toml_model`: with `AGENT_MODEL=x/y` set, `Settings().agent_model == "x/y"`.
- [x] **Step 2: Run** `cd backend; .\.venv\Scripts\python -m pytest tests/test_config.py -v`. Expected: the first two FAIL, the third passes (existing behavior).
- [x] **Step 3: Implement.** Change the defaults and add the settings in `config.py`. Rewrite `agent.toml` as `model = "deepseek/deepseek-v4.1-flash"`, `temperature = 0`, `reasoning_effort = "low"`, `max_attempts = 2`, with one comment line per setting, plus commented `step_retries`, `claim_timeout_s`, `max_steps`, `run_max_steps`. In `build_agent`, pass `timeout=settings.agent_claim_timeout_s` and `max_retries=settings.agent_step_retries` to `ChatOpenAI`.
- [x] **Step 4: Run** the full backend suite. Expected: all pass (existing tests that relied on `max_attempts=3` take their value as a parameter, so they keep passing; fix any that read the default).
- [x] **Step 5: Commit** `config: default to deepseek-v4.1-flash, low effort; harness settings`.

### Task 1: Section loader (R4)

**Files:**
- Create: `backend/app/documents.py`
- Modify: `backend/app/reference.py` (`ALLOWED_FILES` gains `"claims_manual.md"`; new property `manual`)
- Test: `backend/tests/test_documents.py`, `backend/tests/test_reference.py`

**Interfaces:**
- Produces:
  - `DOCUMENTS = {"policy_wording": "policy_wording.md", "claims_manual": "claims_manual.md"}`
  - `@dataclass(frozen=True) class Section: number: int; title: str; text: str`
  - `class Documents: __init__(self, ref: Reference)`; `sections(document: str) -> list[Section]`; `section(document: str, number: int) -> Section` (raises `KeyError` for an unknown document or number); `search(query: str, limit: int = 5) -> list[dict]` returning `{"document", "section", "title", "snippet"}`; `version(document: str) -> str`.

- [x] **Step 1: Write the failing tests:**
  - `test_wording_has_twelve_sections`: titles of `sections("policy_wording")` start `["Your plan", "Trip Cancellation and Interruption", "Trip Delay", ...]`, `len == 12`, numbers 1..12.
  - `test_manual_has_fourteen_sections`: `len == 14`, section 10 title `"Meals without receipts"`.
  - `test_section_text_stops_at_next_heading`: `section("claims_manual", 9).text` contains `"third claim"` and not `"## 10."`.
  - `test_unknown_document_or_section_raises_key_error`: `section("claims_manual", 99)`, `section("evals", 1)`, `section("../evals/decisions.json", 1)` each raise `KeyError`.
  - `test_search_finds_wild_animals_in_manual_section_7`: first hit for `"wild animal"` is `{"document": "claims_manual", "section": 7, ...}` and the snippet contains `"wild animal"` (case-insensitive).
  - `test_search_with_no_match_returns_empty_list`.
  - `test_versions`: `version("policy_wording") == "2026.1"`, `version("claims_manual") == "2026.2"`.
  - In `test_reference.py`: `test_manual_is_allowed` (`ref.manual` starts with `"# Marvin FartInsurance Claims Handling Manual"`) and the existing whitelist test still refuses `"evals/decisions.json"`.
- [x] **Step 2: Run** `pytest tests/test_documents.py tests/test_reference.py -v`. Expected: FAIL (module missing, `manual` missing).
- [x] **Step 3: Implement.** Split on lines matching `^## (\d+)\. (.+)$`. A section's text runs to the next such heading. The version is the first `Version (\d{4}\.\d+)` match in the file. Search: lower-case substring match of the whole query on each section's text, ranked by match count, snippet of about 160 characters around the first match. Documents are read only through `Reference`, never by path.
- [x] **Step 4: Run** the same tests. Expected: PASS.
- [x] **Step 5: Commit** `feat: section loader for the policy wording and the claims manual`.

### Task 2: Lookup tools replace the whole wording (R4)

**Files:**
- Modify: `backend/app/tools.py`, `backend/app/agent.py` (`_emit_message`: drop the `get_policy_wording` special case; add a `read_section` summary)
- Test: `backend/tests/test_tools.py`, `backend/tests/test_agent.py`

**Interfaces:**
- Consumes: `Documents` from Task 1.
- Produces: tools `list_sections(document: str) -> str`, `read_section(document: str, section: int) -> str`, `search_documents(query: str) -> str`. `build_tools` no longer returns `get_policy_wording`. A `read_section` tool result in the trace is `"[<document> section <n>: <title>, <k> characters]"`, never the text.

- [x] **Step 1: Write the failing tests** in `test_tools.py`:
  - `test_tools_offer_lookups_not_whole_wording`: tool names include `list_sections`, `read_section`, `search_documents` and not `get_policy_wording`.
  - `test_read_section_returns_text`: `read_section.invoke({"document": "claims_manual", "section": 10})` contains `"$35"`.
  - `test_bad_lookups_return_error_strings` (Review Focus 5): `read_section` with `("claims_manual", 99)`, `("evals", 1)` and `("../evals/decisions.json", 1)` each return a string starting `"Error:"` that names the two valid documents, and nothing is raised.
  - `test_search_documents_returns_json_hits`: `json.loads(search_documents.invoke({"query": "meals without receipts"}))[0]["section"] == 10`.
  - In `test_agent.py`: `test_section_text_is_not_copied_to_the_trace`: a `FakeToolModel` script calls `read_section` then returns a `Decision`; the emitted `tool_result` content for `read_section` matches `^\[claims_manual section 7: Interpretation notes, \d+ characters\]$`.
- [x] **Step 2: Run** `pytest tests/test_tools.py tests/test_agent.py -v`. Expected: the new tests FAIL.
- [x] **Step 3: Implement** the three tools around `Documents`, catching `KeyError` to return `"Error: ... Documents: policy_wording, claims_manual."`. Remove `get_policy_wording`. Change the trace summary in `_emit_message`.
- [x] **Step 4: Run** the full backend suite. Expected: PASS. Fix tests that listed `get_policy_wording`.
- [x] **Step 5: Commit** `feat: agent looks up sections of the wording and the manual instead of reading the whole wording`.

### Task 3: Schema for runs, to-do items and attempts, with in-place upgrade

**Files:**
- Modify: `backend/app/db.py`
- Test: `backend/tests/test_db.py`

**Interfaces:**
- Produces:
  - New tables: `runs(run_id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL, cap INTEGER, scope TEXT, model TEXT NOT NULL, summary TEXT, error TEXT, usage TEXT, started_at TEXT DEFAULT CURRENT_TIMESTAMP, finished_at TEXT)`; `todo_items(item_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, position INTEGER NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, claim_id TEXT, note TEXT)`; `run_events(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)`. `scope` is a JSON list of claim ids or NULL for the whole queue.
  - New columns, added by `migrate(conn)` when missing: `claims.attempts INTEGER NOT NULL DEFAULT 0`, `claims.last_error TEXT`, `claims.run_id INTEGER`, `trace_events.run_id INTEGER`, `decisions.manual_version TEXT`, `decisions.wording_version TEXT`.
  - `init_schema(conn)` runs `SCHEMA` then `migrate(conn)`. `reset_analyses(conn)` also empties `runs`, `todo_items`, `run_events`, and sets `attempts = 0, last_error = NULL, run_id = NULL` on every claim.

- [x] **Step 1: Write the failing tests:**
  - `test_init_creates_harness_tables`: the three tables exist; the six columns exist (`PRAGMA table_info`).
  - `test_pilot_database_upgrades_in_place` (Review Focus 4): create a DB with the pilot `SCHEMA` text (copy it into the test as a constant), insert a claim, a decision and a trace event, call `init_schema`, then the rows are still there and the new columns read their defaults (`attempts == 0`).
  - `test_init_schema_twice_is_harmless`.
  - `test_reset_clears_runs_and_attempts`.
  - `test_decision_cannot_be_written_twice`: a second plain `INSERT INTO decisions` for the same `claim_id` raises `sqlite3.IntegrityError`.
- [x] **Step 2: Run** `pytest tests/test_db.py -v`. Expected: the new tests FAIL (the last one passes already: `claim_id` is the primary key; keep it as a guard).
- [x] **Step 3: Implement.** `migrate` reads `PRAGMA table_info(<table>)` and runs `ALTER TABLE ... ADD COLUMN` for each missing column.
- [x] **Step 4: Run** the full backend suite. Expected: PASS.
- [x] **Step 5: Commit** `feat(db): runs, to-do items, run events, attempts; upgrade pilot databases in place`.

### Task 4: `work_claim`, the per-claim worker with attempt rules (R3, R5)

**Files:**
- Modify: `backend/app/runner.py` (replace `run_claim` and `run_queue`), `backend/app/agent.py`
- Rename and extend: `backend/app/instructions.md` → `backend/app/instructions_claim.md`
- Test: `backend/tests/test_runner.py` (rewrite), `backend/tests/test_agent.py`, `backend/tests/fakes.py`

**Interfaces:**
- Consumes: Task 3 columns, `Documents.version`.
- Produces:
  - `work_claim(conn, ref, agent: Agent, claim_id: str, run_id: int | None = None, max_attempts: int = 2) -> str`, returning one line:
    - `"CLM-1001: approve $420.00, paid"` / `"CLM-1003: approve $3,750.00, held for sign-off"` / `"CLM-1005: decline"` / `"CLM-1009: refer"`
    - `"CLM-1017: attempt 1 of 2 failed (ValueError: ...). It is back in the queue: try it again after the others."`
    - `"CLM-1013: attempt 2 of 2 failed (...). Left for a handler."`
    - `"CLM-1002: not worked, the claim is decided."` (any status other than `waiting`; also unknown ids: `"not worked, there is no such claim."`)
  - Claim `status`, `attempts`, `last_error`, `run_id` kept up to date. Every trace event of the claim carries `run_id`.
  - `load_instructions()` is replaced by `load_instructions(name: Literal["claim", "run"]) -> str` reading `instructions_<name>.md`.
  - `ScriptedAgent` fails on purpose: `CLM-1013` always raises `RuntimeError("scripted failure")`; `CLM-1017` raises the same only while the claim's `attempts` is 0.
  - `LangChainAgent.__init__` gains `max_steps: int = 40, timeout_s: float = 300`; `decide` passes `{"recursion_limit": max_steps}` and raises `TimeoutError` when the stream runs past `timeout_s`.
- Rules (from `docs/plans/HARNESS.md` R5):
  1. Take the claim with `UPDATE claims SET status='in_progress', run_id=? WHERE claim_id=? AND status='waiting'`. No row changed means not worked.
  2. Any exception from `agent.decide`, and a payment whose amount differs from the decided payout, is a failed attempt: delete the claim's payment, `attempts += 1`, `last_error = message`, trace `error`. Then status `waiting` if `attempts < max_attempts`, else `needs_handler`.
  3. On success: payment through `request_payment` (approve) or voided (decline/refer) as in the pilot, a plain `INSERT` into `decisions` with `manual_version` and `wording_version`, status `decided` or `awaiting_approval`, trace `decision` and `usage`.
  4. If the claim has a to-do item in the run, set it to `in_progress` when work starts, then `done`, `todo` (back in the queue) or `left_for_handler` with the error as note. (The to-do helper comes from Task 5; until then, guard with `if run_id`. Wire it in Task 5 Step 3.)

- [x] **Step 1: Write the failing tests** (stub agents in `fakes.py`: `StubAgent(outcomes: dict[str, list[Decision | Exception]])` pops one outcome per call and records the claim ids it was asked for):
  - `test_approve_under_limit_is_paid_and_decided`: line `"CLM-1001: approve $420.00, paid"`, status `decided`, decision row has `manual_version == "2026.2"`, `wording_version == "2026.1"`.
  - `test_over_limit_is_held` → `awaiting_approval`, payment `held`.
  - `test_first_failure_goes_back_to_waiting`: status `waiting`, `attempts == 1`, `last_error` names the error, line contains `"back in the queue"`, no decision row.
  - `test_second_failure_leaves_it_for_a_handler`: status `needs_handler`, `attempts == 2`, line contains `"Left for a handler"`, no decision row, no payment.
  - `test_failed_attempt_drops_its_held_payment` (Review Focus 2): the stub calls `request_payment(conn, id, 2000, 1000)` before raising; afterwards no payment row exists for the claim.
  - `test_payment_mismatch_is_a_failed_attempt`.
  - `test_decided_claim_is_not_worked_again` (Review Focus 1): line `"... not worked, the claim is decided."`, the stub was not called, `attempts` unchanged.
  - `test_unknown_claim_is_not_worked`.
  - `test_trace_events_carry_run_id`.
  - `test_claim_usage_sums_attempts` (move from the pilot's retry test).
  - In `test_agent.py`: `test_scripted_agent_fails_on_1013_always_and_1017_once`; `test_langchain_agent_passes_recursion_limit` (spy on the compiled agent's `stream` config); `test_langchain_agent_times_out` (a fake model whose `_generate` sleeps past a 0.05 s `timeout_s`, expect `TimeoutError`).
- [x] **Step 2: Run** `pytest tests/test_runner.py tests/test_agent.py -v`. Expected: FAIL (`work_claim` missing).
- [x] **Step 3: Implement** `work_claim` and the agent changes. Delete `run_claim`, `run_queue`, `_refer` and the in-place retry loop. Rename the instructions file with `git mv`, then extend it: follow the manual's working order (manual section 4), look up sections with `search_documents`, `list_sections` and `read_section` rather than guess, always read manual section 9 and the customer's history before deciding, and cite the wording section and, where used, the manual section. Write only from the wording and the manual.
- [x] **Step 4: Run** the backend suite. `test_api.py` and `test_score.py` still call the old functions; mark those tests `xfail(reason="rewired in Task 7/10")` only if they cannot be fixed in one line, and list them in the ledger.
- [x] **Step 5: Commit** `feat: work_claim with attempt rules, one-line reports and versions on decisions`.

### Task 5: Runs, to-do list and the missed-claim check (R1, R2, R5)

**Files:**
- Create: `backend/app/runs.py`
- Modify: `backend/app/runner.py` (wire the to-do updates)
- Test: `backend/tests/test_runs.py`

**Interfaces:**
- Produces:
  - `class RunActive(Exception)`
  - `start_run(conn, model: str, cap: int | None = None, scope: list[str] | None = None) -> int`: one `INSERT ... SELECT ... WHERE NOT EXISTS (SELECT 1 FROM runs WHERE status='running')`; raises `RunActive` when no row is inserted.
  - `run_event(conn, run_id, kind: str, content: dict) -> None`
  - `set_todo(conn, run_id, items: list[dict]) -> list[dict]`: replaces the run's items; each input is `{"title": str, "claim_id": str | None}`; positions 1..n, status `todo`.
  - `update_todo(conn, run_id, item_id: int, status: str, note: str | None = None) -> dict`: raises `ValueError` for an unknown item or status, or for `done` on an item whose claim is not `decided` or `awaiting_approval`.
  - `todo_for_claim(conn, run_id, claim_id, status, note=None) -> None`: used by `work_claim`; appends an item when the claim has none.
  - `in_scope_waiting(conn, run_id) -> list[str]`: waiting claims in the run's scope, by claim id.
  - `cap_reached(conn, run_id) -> bool`: the number of distinct claims with `claims.run_id = run_id` has reached `cap`.
  - `missed_claims(conn, run_id) -> list[str]`: claims of this run that are `in_progress` or back to `waiting` (a retry is due), plus, while the cap is not reached, waiting claims in scope not yet taken, up to the cap's remaining room.
  - `finish(conn, run_id, status: str, summary: str | None = None, error: str | None = None) -> None`: sets `finished_at`, stores the total usage (the orchestrator's usage passed in `run_event(..., "usage", ...)` plus every claim `usage` trace event with this `run_id`), and puts any `in_progress` claim of the run back to `waiting`.
  - `recover_after_restart(conn) -> None`: runs still `running` become `failed` with error `"the server stopped during the run"`; `in_progress` claims go back to `waiting` with a `status` trace event `{"status": "waiting", "note": "recovered after a restart"}` (the trace is kept, `attempts` unchanged).

- [x] **Step 1: Write the failing tests:**
  - `test_second_run_is_refused_while_one_runs` → `RunActive`.
  - `test_set_and_update_todo` (positions, statuses), `test_update_todo_rejects_unknown_status_and_item`, `test_item_cannot_be_done_for_undecided_claim`.
  - `test_work_claim_ticks_its_todo_item` (success → `done`; first failure → `todo`; second → `left_for_handler` with the note).
  - `test_cap_counts_claims_not_attempts`: cap 2, one claim failing once and retried, `cap_reached` only after the second distinct claim.
  - `test_missed_claims_lists_untried_and_retries_within_cap`: scope of 5, cap 3, nothing done → 3 ids; after 3 decided → `[]`; a claim back to `waiting` after a first failure is listed.
  - `test_finish_puts_in_progress_back_and_sums_usage`.
  - `test_recover_after_restart`.
- [x] **Step 2: Run** `pytest tests/test_runs.py -v`. Expected: FAIL.
- [x] **Step 3: Implement** `runs.py`; call `todo_for_claim` from `work_claim` when `run_id` is set.
- [x] **Step 4: Run** the backend suite. Expected: PASS.
- [x] **Step 5: Commit** `feat: runs, to-do list and missed-claim check`.

### Task 6: The orchestrator (R1, R2, R3, R5, R7)

**Files:**
- Create: `backend/app/orchestrator.py`, `backend/app/instructions_run.md`
- Test: `backend/tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `work_claim` (Task 4), `runs.py` (Task 5), `build_agent` (Task 0).
- Produces:
  - `build_run_tools(conn, ref, agent: Agent, run_id: int, max_attempts: int) -> list[BaseTool]`:
    - `list_waiting_claims()`: JSON list of `{claim_id, claim_type, summary, amount_claimed, attempts}` for `in_scope_waiting`, plus `"cap"` and `"taken"` counts.
    - `set_todo(items: list[TodoInput])` and `update_todo(item_id: int, status: str, note: str = "")`: return the whole list as JSON, or `"Error: ..."` from `ValueError`.
    - `work_claim(claim_id: str)`: refuses with `"CLM-x: not worked, the run's cap of N claims is reached."` when `cap_reached` and the claim is not already one of the run's claims, and `"CLM-x: not worked, it is not part of this run."` outside the scope; otherwise returns `runner.work_claim(...)`.
    - `finish_run(summary: str)`: if `missed_claims` is not empty returns `"Not finished. Still to work: CLM-..., CLM-..."`; otherwise stores the summary and returns `"Run finished."`.
  - `class Orchestrator(Protocol): model_id: str; usage: dict | None; def run(self, tools: list[BaseTool], on_event: Emit) -> None`
  - `LangChainOrchestrator(model, model_id, max_steps)`: one `create_agent(model, tools, system_prompt=load_instructions("run"))`, prompt `"Work the claims queue."`, `recursion_limit = max_steps`, streams `tool_call`, `tool_result` and `message` events to `on_event`, keeps `usage`.
  - `ScriptedOrchestrator`: deterministic, no model. Lists the waiting claims, sets one to-do item per claim up to the cap, works them in claim id order, works the claims that came back to the queue once more, then calls `finish_run` with a summary built from the one-line reports.
  - `build_orchestrator(settings) -> Orchestrator`: scripted when `agent_provider == "scripted"`.
  - `run_harness(conn, ref, settings, run_id: int, orchestrator: Orchestrator | None = None, agent: Agent | None = None) -> None`: builds the tools and runs the orchestrator; catches any exception from it; then, if the run is not finished, calls `runs.finish(..., status="failed", error=<exception or "missed: ...">)`. Never raises.
- `instructions_run.md`: plan with `set_todo` after `list_waiting_claims`; choose the order and say why in one line; work each claim with `work_claim`; keep the list current with `update_todo`; a claim back in the queue is worked again after the others; respect the cap and do not touch claims outside it; call `finish_run` with a summary for handlers (counts by outcome, payouts held for sign-off, claims left for a handler and why); if `finish_run` refuses, work the listed claims and call it again. No claim ids or examples from the queue.

- [x] **Step 1: Write the failing tests** (an `agent` that is `StubAgent` or `ScriptedAgent`; an orchestrator that is `ScriptedOrchestrator` or `LangChainOrchestrator` over `FakeToolModel` scripts):
  - `test_scripted_run_with_cap_works_that_many_and_leaves_the_rest`: cap 5 on the seeded queue → 5 claims not `waiting`, 20 `waiting`, run `finished`, summary not empty.
  - `test_run_without_cap_works_every_waiting_claim`.
  - `test_run_on_one_named_claim`: scope `["CLM-1004"]` → only it changes.
  - `test_failed_claim_does_not_stop_the_run`: scope of 5 with one claim always failing → the other 4 decided, the failing one `needs_handler`, run `finished`.
  - `test_claim_failing_once_is_retried_in_the_same_run_and_decided`.
  - `test_work_claim_tool_refuses_past_the_cap_and_outside_scope` (Review Focus 1).
  - `test_finish_run_refuses_while_claims_are_missed`.
  - `test_langchain_orchestrator_sees_only_one_line_reports`: a `FakeToolModel` script calls `work_claim` then `finish_run`; the messages the model was shown hold the one-line report and none of the subagent's tool calls (no `get_claim` text, no `<claim_data>`).
  - `test_each_claim_gets_a_fresh_subagent`: with `LangChainAgent` over a `FakeToolModel` that records `seen`, the first model call for the second claim has exactly two messages (system and the human prompt).
  - `test_orchestrator_crash_fails_the_run_and_frees_claims` (Review Focus 3): an orchestrator that raises after one `work_claim` → run `failed`, `error` set, no claim `in_progress`.
  - `test_orchestrator_stopping_early_records_missed_claims`: a `FakeToolModel` that ends with a plain message without calling `finish_run` → run `failed`, error lists the missed ids.
  - `test_run_usage_sums_orchestrator_and_claims`.
- [x] **Step 2: Run** `pytest tests/test_orchestrator.py -v`. Expected: FAIL.
- [x] **Step 3: Implement** `orchestrator.py` and `instructions_run.md`.
- [x] **Step 4: Run** the backend suite. Expected: PASS.
- [x] **Step 5: Commit** `feat: orchestrator that plans and works the queue through a to-do list`.

### Task 7: API (R1, R2, R4, R5, R7)

**Files:**
- Modify: `backend/app/api.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Produces:
  - `POST /runs` body `{"claim_ids": [..]?, "limit": int?}` → 202 `{"run_id": n}`; 404 for an unknown claim id; 409 `"a run is already in progress"`; 422 for `limit < 1`. Starts `run_harness` in a background task with its own connection.
  - `GET /runs/latest` → `{"run": {run_id, status, cap, model, summary, error, usage, started_at, finished_at}, "todo": [{item_id, position, title, status, claim_id, note}]}` or `{"run": null, "todo": []}`.
  - `GET /runs/{run_id}` → same shape plus `"events"` (the run events in order); 404 when unknown.
  - `GET /claims` items gain `attempts`. `GET /claims/{id}` gains `read: [{"document", "section", "title"}]` (from `read_section` tool calls in the trace, in order, without repeats), `attempts`, `last_error`, `decision.manual_version`, `decision.wording_version`, and `usage` becomes the sum of the claim's `usage` events.
  - `GET /instructions` → `{"run": str, "claim": str}`.
  - Removed: `POST /claims/{id}/run`, `POST /queue/run`.
  - Lifespan calls `runs.recover_after_restart` instead of deleting stuck traces.

- [x] **Step 1: Write the failing tests** (scripted provider, `TestClient`):
  - `test_post_runs_with_limit_works_that_many`: `limit: 3` → after the background task, 3 claims not `waiting`, `GET /runs/latest` shows `finished` and 3 to-do items `done`.
  - `test_post_runs_with_claim_ids`.
  - `test_second_run_conflicts` (insert a `running` row first) → 409.
  - `test_post_runs_unknown_claim_404`, `test_post_runs_limit_zero_422`.
  - `test_runs_latest_empty`.
  - `test_needs_handler_claim_after_run`: run on `["CLM-1013"]` → claim status `needs_handler`, `last_error` set, no decision.
  - `test_claim_detail_lists_what_was_read` (a stub agent that emits two `read_section` calls, one repeated).
  - `test_instructions_returns_both`.
  - `test_restart_recovers_running_run` (insert a `running` run and an `in_progress` claim, start the app) → run `failed`, claim `waiting`, its old trace kept.
  - Rewrite the pilot tests that used the removed routes to use `POST /runs`.
- [x] **Step 2: Run** `pytest tests/test_api.py -v`. Expected: FAIL.
- [x] **Step 3: Implement.** Remove `RETRY_DELAY_S` and the old run helpers.
- [x] **Step 4: Run** the backend suite. Expected: PASS, no `xfail` left from Task 4 except `test_score.py` (Task 10).
- [x] **Step 5: Commit** `feat(api): runs, to-do list, what the agent read, both instructions`.

### Task 8: Front end (R1, R2, R4, R5, R7)

What changes on each screen:

| Screen | Change |
|---|---|
| Queue | To-do panel for the current or latest run (status, cap, cost, summary, items with status marks); a "Limit" number field next to "Run queue"; buttons disabled while a run is `running`; a "Needs a handler" tile and badge; a waiting claim that failed once shows "attempt 1 of 2 failed" under its summary |
| Claim detail | "What the agent read" card in the rail (document, section, title, in order); manual and wording versions under the decision; attempts and last error on a `needs_handler` claim, in a notice like the held-for-approval one; "Run this claim" starts a run scoped to the claim and is disabled while a run is `running` |
| Agent activity (`Trace.tsx`) | A `read_section` call reads "Read claims manual, section 7" and its result shows the bracketed summary; `search_documents` and `list_sections` get one-line summaries; failed attempts show as `error` rows with "Attempt 1 of 2 failed"; the pilot's `retry` row style is removed; a `status` row with "recovered after a restart" is shown plainly |
| Instructions | Two sections, "Running the queue" and "Working a claim", each rendered with the existing Markdown renderer |
| Approvals, nav | No change in behavior. Check the nav count still works with the new statuses |

**Files:**
- Modify: `frontend/src/api.ts`, `frontend/src/format.ts`, `frontend/src/components/StatusBadge.tsx`, `frontend/src/pages/Queue.tsx`, `frontend/src/pages/ClaimDetail.tsx`, `frontend/src/pages/Trace.tsx`, `frontend/src/pages/Instructions.tsx`, `frontend/src/styles/app.css`, `frontend/src/testUtils.tsx`
- Create: `frontend/src/components/TodoPanel.tsx`
- Test: `Queue.test.tsx`, `ClaimDetail.test.tsx`, `Trace.test.tsx`, `Instructions.test.tsx`, `TodoPanel.test.tsx`, `App.test.tsx`

**Interfaces:**
- Consumes: Task 7 routes.
- Produces: `Status` gains `'needs_handler'`; `STATUS_LABEL.needs_handler = 'Needs a handler'`; `startRun(opts: {claimIds?: string[]; limit?: number}) => Promise<{run_id: number}>`; `getLatestRun() => Promise<{run: Run | null; todo: TodoItem[]}>`; `getInstructions() => Promise<{run: string; claim: string}>`; types `Run`, `TodoItem`, `ReadSection`. `runClaim` and `runQueue` are removed.

- [x] **Step 1: Write the failing tests:**
  - `TodoPanel.test.tsx`: renders each item with its status label (`To do`, `In progress`, `Done`, `Left for a handler`) and note; shows the run's status, cost and summary; shows "No run yet" when `run` is null.
  - `Queue.test.tsx`: the panel shows the latest run and updates on the next poll; "Run queue" with the limit field set to 5 calls `startRun({limit: 5})`; ticked claims call `startRun({claimIds: [...]})`; a 409 shows "A run is already in progress"; the "Needs a handler" tile counts `needs_handler` claims; the run button is disabled while the latest run is `running`.
  - `ClaimDetail.test.tsx`: a "What the agent read" list with "Claims manual, section 7: Interpretation notes"; versions under the decision; attempts and the last error on a `needs_handler` claim; "Run this claim" calls `startRun({claimIds: [id]})`.
  - `Instructions.test.tsx`: two headings, "Running the queue" and "Working a claim", each with its rendered text.
  - `Trace.test.tsx`: a `read_section` call renders "Read claims manual, section 7"; an `error` event with "Attempt 1 of 2 failed" renders as an error row; the old `retry` test is removed.
  - `ClaimDetail.test.tsx` (also): "Run this claim" is disabled while the latest run is `running`.
  - `App.test.tsx`: the nav still renders with a `needs_handler` claim in the mock data.
- [x] **Step 2: Run** `cd frontend; npx vitest run`. Expected: the new tests FAIL.
- [x] **Step 3: Implement**, following `docs/DESIGN.md`: the to-do panel sits above the queue table as a checklist card, items with status marks matching the stamps, and the summary as a short note under it. `needs_handler` gets a badge and tile style.
- [x] **Step 4: Run** `npx vitest run` and `npx tsc -b`. Expected: all pass, no type errors.
- [x] **Step 5: Commit** `feat(frontend): to-do list, run limit, what the agent read, both instructions`.

### Task 9: End-to-end tests

**Files:**
- Modify: `e2e/tests/01-queue.spec.ts`, `e2e/tests/02-approval.spec.ts`
- Create: `e2e/tests/03-harness.spec.ts`

Scripted outcomes on the seeded queue: `CLM-1003`, `CLM-1008`, `CLM-1011` held for sign-off; `CLM-1013` fails twice and needs a handler; `CLM-1017` fails once, then is decided.

- [x] **Step 1: Update and write the specs:**
  - `01`: ticking two claims still works; "Run queue" then ends with 0 waiting, 3 awaiting approval, 1 needing a handler, 21 decided; the instructions page shows both headings.
  - `02`: unchanged flow, with the run started through the new button.
  - `03-harness` (runs on a fresh DB; reset it in `beforeAll` through a test-only helper script, `e2e/scripts/clean.mjs`, as today): with the limit set to 3, the to-do panel shows three items that end `Done`, 22 claims stay waiting, the summary appears; opening a decided claim shows "What the agent read".
- [x] **Step 2: Run** `cd e2e; npx playwright test`. Expected: all pass. Run it three times to check for flakes.
- [x] **Step 3: Commit** `test(e2e): runs, to-do list, needs a handler`.

Note: the scripted agent does not call `read_section`, so make `ScriptedAgent` emit one `read_section` tool call and result (wording section 10) per claim before this task, with a unit test in `test_agent.py`.

### Task 10: Scoring, one-command check and documents

**Files:**
- Modify: `scripts/score.py`, `backend/tests/test_score.py`, `backend/tests/test_evals_isolation.py`, `docs/SPEC.md`, `docs/TECH_STACK.md`, `README.md`, `COMMANDS.md`, `data/README.md`
- Create: `package.json` (root), `scripts/check.mjs`

- [x] **Step 1: Write the failing tests:**
  - `test_score.py`: `_run_agent` uses `work_claim` and still returns one decision per claim in a temp DB (scripted provider).
  - `test_evals_isolation.py`: `test_instructions_quote_no_claim`: neither `instructions_run.md` nor `instructions_claim.md` matches `CLM-\d{4}`.
- [x] **Step 2: Run** `pytest tests/test_score.py tests/test_evals_isolation.py -v`. Expected: FAIL.
- [x] **Step 3: Implement.** `score.py` calls `work_claim(conn, ref, agent, id, None, settings.agent_max_attempts)` per claim. Root `package.json` has `"check": "node scripts/check.mjs"`; `check.mjs` runs, in order and stopping at the first failure: backend pytest (Windows or POSIX venv path), `npx vitest run` and `npx tsc -b` in `frontend`, `npx playwright test` in `e2e`.
- [x] **Step 4: Update the documents:** `SPEC.md` sections 3.1 (manual as an input), 3.3, 5 (`needs_handler`), 6 (to-do list, what the agent read), 8 (manual leaves "out of scope"), 9 (runs, cap, attempts), 10; `TECH_STACK.md` (orchestrator, subagents, runs on LangChain, default model); `README.md` (status of the product, `npm run check`, run limit); `COMMANDS.md`; `data/README.md` (the agent now reads the manual).
- [x] **Step 5: Run** `npm run check` from the repo root. Expected: every suite green.
- [x] **Step 6: Commit** `docs+chore: one-command check, scoring through work_claim, docs for the harness`.

### Task 11: Live run on the real model

Needs the OpenRouter key in `.env` and costs a few cents. Ask before running.

- [x] **Step 1:** Stop any dev server using `farting.db`, then `.\backend\.venv\Scripts\python scripts\reset_db.py`.
- [x] **Step 2:** Start the backend and front end (`COMMANDS.md`), open the queue, set the limit to 5 and press "Run queue".
- [x] **Step 3:** Check, and record in this file: the to-do list fills and ticks off; 5 claims leave `waiting` and 20 stay; each of the 5 claim pages shows its steps and "What the agent read"; the run summary and cost appear. Screenshot the queue page.
- [x] **Step 4:** Note the run's cost and any claim that needed a retry or a handler. Do not tune the instructions from `data/evals/`.
- [ ] **Step 5:** Optional, with the user's OK: `scripts/score.py --split queue --run` and record the result next to the pilot's in `README.md`.

---

**Result (2026-10-30, `deepseek/deepseek-v4.1-flash`, low effort, Limit 5, after reseeding):** run 1 finished in about 8 minutes for $0.027 (50 model calls). The agent planned six items (five claims and the summary) and chose CLM-1001, CLM-1004, CLM-1013, CLM-1019 and CLM-1023, "the smallest/simplest" in its words. Outcomes: CLM-1001 approve $420.00 and CLM-1004 approve $186.00 (both paid), CLM-1013 decline, CLM-1019 and CLM-1023 refer. No failed attempts, nothing left for a handler, the other 20 claims stayed waiting. Every claim page lists what it read: 7 to 18 sections each, from both the wording and the manual. The summary itself flagged that two nine-hour delays were referred while a seven-hour one was declined. Step 5 (scoring) not run.

## Self-review

- Spec coverage: R1 → Tasks 5, 6, 7, 8. R2 → Tasks 5, 6, 8, 9. R3 → Tasks 4, 6. R4 → Tasks 1, 2, 7, 8. R5 → Tasks 3, 4, 5, 6, 7. R6 → Task 0. R7 → Tasks 4, 6, 7, 8, 10. Docs and "done looks like" → Tasks 10 and 11.
- Names used across tasks: `work_claim`, `start_run`, `set_todo`, `update_todo`, `todo_for_claim`, `missed_claims`, `cap_reached`, `in_scope_waiting`, `finish`, `recover_after_restart`, `run_harness`, `build_orchestrator`, `load_instructions(name)`, `Documents`. Each is defined once in an Interfaces block.
