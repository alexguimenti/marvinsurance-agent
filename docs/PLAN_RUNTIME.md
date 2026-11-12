# Sending claims back and the agent on AgentCore Runtime: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A senior handler can sign off a large approval or send it back to the agent with a note, the agent becomes an AgentCore app that runs the same way on a laptop and on AgentCore Runtime, and the app becomes the one place that keeps one piece of the agent's work going at a time.

**Architecture:** The claim graph pauses with `interrupt()` in a node of its own when an approval waits for sign-off, and resumes with the handler's answer read from the app. The agent's FastAPI service becomes `agent/entrypoint.py`, a `BedrockAgentCoreApp` with one entrypoint whose payload names an operation; work runs on a thread registered as an async task, so Runtime keeps the session while work is going. The app reaches it over HTTP (laptop, Bearer key) or with boto3's `invoke_agent_runtime` (Runtime). The app creates runs and rounds and records reviews itself, under a lock row, and the agent reports that it is alive every minute.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy Core, LangGraph, `bedrock-agentcore` (`BedrockAgentCoreApp`, `requires_api_key`), boto3/botocore, Starlette `TestClient`; React + Vitest; Playwright.

**Spec:** `docs/RUNTIME.md`

SIDENOTE: For consistency with the course, please read `labs/week3day4_lab2.ipynb`, which deploys the agent on AgentCore Runtime and points the app at it, without running it. Where the notebook and this document differ, follow this document. The pause in step 1 was in the course's build for self-improvement, before the security build took it out: `git show week2day5-end:agent/harness.py` has its `wait_for_sign_off` node, and `git show week2day5-end:agent/service.py` its `resume_claim` route. Lab 1's `agentcore dev` also serves on port 8080, so stop it before `npm run dev`. Include this sidenote in your plan too. (In this repo the notebook is at `../undercover/labs/week3day4_lab2.ipynb`, and the `git show` refs are branches of `../undercover`.)

## Global Constraints

- Our layout: the Python root is `backend/` (`app/`, `agent/`, `context/`, `tests/`, `pyproject.toml`, `uv.lock`); the dashboard is `frontend/`. The spec's `backend/instructions/claim.md` is our `backend/agent/instructions_claim.md`, and its `dashboard/src` is `frontend/src`.
- `agentcore/` and `runtime/` are the platform team's: copy them byte for byte from `../undercover` at `origin/week3day4-start` (`git show`), and never edit them.
- Agent payloads: `{"operation": "<operation id>", ...inputs}`, no optional inputs. They answer `{"accepted": true}`, `{"refused": "<reason>"}` or, for `health`, `{"healthy": true}`. An unknown operation or a bad input answers 400 with what is wrong.
- The app→agent call has a 10 s timeout on the HTTP route. On the Runtime route it makes 1 attempt, waits 5 s to connect and 20 s to read, and uses a session id of at least 33 characters, new for each request.
- Heartbeat: the agent reports every 60 s. The app treats work it has not heard from for 300 s as stopped, with the reason `"The agent stopped answering"`.
- A send-back note is required and at most 1,000 characters.
- New claim status: `sent_back`.
- The agent's checkpoint pool uses `prepare_threshold=None`.
- Tests never call a model, AWS, Supabase Auth or the Gateway. `UNDERCOVER_AGENT_RUNTIME` is blanked in `conftest.py`, in `scripts/check.mjs` and in the e2e backend's environment.
- Commit at the end of each phase. Do the live checks last, after one `npm run seed`.

## Rulings made while planning

- **The deployment package (the owner chose this).** `agentcore/cdk/lib/cdk-stack.ts` stages `agent/` from the repository root and runs `uv export --only-group runtime` there. Ours are `backend/agent/` and `backend/pyproject.toml`. **As the platform team wrote it, `agentcore deploy` cannot package our agent.** The stack needs to stage `backend/agent` and run the export in `backend/`, or our Python root has to move. Nothing in this change deploys, so this goes on record here and in `docs/STATUS.md`. The product does what it can on its side:
  - The agent stops importing anything from `app` (Task 6), so `runtime/` with `agent/` beside it is a complete package.
  - `npm run check` proves this by staging exactly those two folders, as the stack does, and importing `runtime.main` there with only the `runtime` group.
- **The backend's address.** The stack passes `UNDERCOVER_API` (the app's address) to the runtime, while our agent reads `UNDERCOVER_BACKEND`. The agent will read `UNDERCOVER_API` first, then `UNDERCOVER_BACKEND`.
- **The laptop check (step 3's last "done when").** It needs both `--project backend` and the code on the path, so it becomes `PYTHONPATH=backend uv run --project backend --env-file .env --isolated --only-group runtime python -m runtime.main`, run from the repository root.
- **Payload input names.** The operations keep their routes' input names: `start_dispatch` takes `workers`, not the spec's example `agents`.
- **The pause already exists.** Our build kept the pause: `pay_node` loops on `interrupt()`. Task 1 splits the wait into a node of its own, `wait_for_sign_off`, with nothing before the `interrupt()`. `remember` moves after the payment, so a sent-back decision never reaches memory.
- **Where the sign-off and send-back controls go.** The spec's sign-off panel goes on the claim page, as a "Sign off" button and a "Send back" form in the held notice. The Approvals page keeps "Approve payment" and gains a link to the claim, where the send-back form is.
- **The live page checks.** The spec asks for them through the Playwright MCP server. They are done by a script that signs in as Dana (`e2e/live_security.mjs`), as in the last two changes, so the password never passes through a tool call. Screenshots are read back to check the pages.
- **Undoing a review.** To undo a review the agent did not take, the app clears it. An approved change also deletes the version it created. An approved recipe also puts the recipe that was current before back as current; `decide_proposal` records which one that was, in a new `proposals.previous_recipe` column.

## Review Focus

1. **A sign-off and a send-back at the same moment.** Exactly one wins: the other gets 409, and the payment and the decision stay consistent. Task 3 tests this.
2. **An agent that never heard of a send-back.** The app answered, then the agent was down or its session ended. The claim stays `sent_back` with "waiting for the next dispatch", and the next dispatch takes it and resumes its paused thread with the note. Task 3 tests this.
3. **A paused graph resumed twice**, by the app's call and by a dispatch carrying it on. The payment and the record are each written once, and the graph's second resume is refused because the claim is no longer paused. Task 2 tests this.
4. **A start the agent never acknowledged (timeout after send).** Nothing changes, and the run stays running. If the agent never actually started it, the next start after 5 quiet minutes stops it and goes ahead. Task 9 tests this.
5. **Getting ready fails on the first request**, for example Identity unreachable. That request answers with the error, nothing is cached, and the next request tries again. Task 5 tests this.

---

## Task 0: The platform team's folders (before Phase 1)

- [ ] **Copy the folders.** In `../undercover`, run `git archive origin/week3day4-start agentcore runtime | tar -x -C ../undercover_mex`, then check with `git diff --no-index` that the files match. Commit with `chore: the platform team's agentcore/ and runtime/`.

---

## Phase 1: The claim approval comes back (spec step 1)

### Task 1: The pause in a node of its own; memory after the payment; copies never pause

**Files:**
- Modify: `backend/agent/graph.py`, `backend/agent/worker.py` (`claim_paused`; `_clear_finished_copy` keeps working)
- Test: `backend/tests/test_pause.py`

**Interfaces:**
- **Produces, the graph edges:**
  - `decide → pay` for an approval, otherwise `→ remember`.
  - `pay → remember` when paid now.
  - `pay → wait_for_sign_off` when held, on a queue claim.
  - `pay → END` when held, on an evaluation's copy.
  - `wait_for_sign_off` returns `Command(goto="remember")` once the claim is paid, or `Command(goto="model", update=...)` when it was sent back (Task 3). `remember → END`.
- **Produces, the wait node:** the first statement of `wait_for_sign_off` is `interrupt({"claim_id", "payout", "waiting_for": "sign-off"})`. After it, the node reads `api.claim_state(claim_id)`:
  - paid: it traces nothing new, because the sign-off already traced the payment;
  - neither paid nor sent back: it returns `Command(goto="wait_for_sign_off")` and pauses again.
- **Produces:** `worker.claim_paused(env, claim_id) -> bool`, which is True when the claim's thread has `get_state(...).interrupts`.

- [x] **Step 1: Write the failing tests** in `test_pause.py`, using `agent_env`, a stub model and `FakeMemoryStore`.
  - `test_an_approval_over_the_limit_pauses_with_its_place_saved`: CLM-1003, `approve(2000.0)`.
    - `claim_paused` is True and the status is `awaiting_approval`.
    - `get_state(...).next == ("wait_for_sign_off",)`.
    - Nothing is saved to memory yet.
  - `test_a_payment_the_gateway_refused_pauses`: on the stand-in Gateway with the limit at 5000, the claim is paused with `hold_reason == "gateway_refused"`.
  - `test_an_evaluations_copy_does_not_pause`: the copy is held, its thread has no interrupt, and `_clear_finished_copy` deletes the thread.
  - `test_a_resume_after_a_sign_off_ends_the_work_and_saves_to_memory`: sign off, then `invoke(env, ..., Command(resume={}), None)`.
    - The thread is finished and `claim_paused` is False.
    - The store has 2 puts.
    - There is exactly one `paid` status step.
  - `test_an_early_resume_pauses_again`: resume without a sign-off; `claim_paused` is still True.
- [x] **Step 2: Run them.** Run `.venv\Scripts\python -m pytest tests/test_pause.py -q`. Expected: FAIL.
- [x] **Step 3: Implement** to the interfaces. Then update the existing tests that expect `remember` before `pay` (the memory tests for a held claim) to the new order.
- [x] **Step 4: Run them again,** with `test_graph.py`, `test_worker.py` and `test_memory.py`. Expected: all PASS.

### Task 2: `resume_claim` in the service, and the sign-off asks for it

**Files:**
- Modify: `backend/agent/service.py` (the route `POST /claims/{claim_id}/resume`, `operation_id="resume_claim"`), `backend/agent/worker.py` (`resume(env, claim_id) -> str`), `backend/app/payments.py` (`sign_off`), `backend/app/api.py` (the approve route calls the agent), `backend/app/agent_client.py` (`resume_claim(...)`), `docs/STATUS.md` (known gap)
- Test: `backend/tests/test_resume.py`

**Interfaces:**
- **The service route:**
  - It refuses with 409 when the claim is not paused (`"<claim> is not waiting for a handler's answer"`), and when a sent-back claim cannot be taken (Task 3).
  - Otherwise it answers 202 `{"status": "resuming", "claim_id": ...}` at once, works in the background outside any run, on the current version's claim agents, and never takes the service's one-at-a-time lock.
- **`worker.resume(env, claim_id) -> str`:** inside `env.working(claim_id)`, it calls `invoke(env, claim_id, thread_id, Command(resume={}), None)` and then `_settle`.
- **The sign-off (`payments.sign_off`):** the gate is now a conditional update of the claim from `awaiting_approval` to `decided`, checked by row count, and it runs before the payment update, in the same transaction. A claim that is no longer waiting answers 409 (`AlreadySettled`).
- **The approve route:** after the sign-off it calls `agent_client.resume_claim(http, settings, claim_id)`. If that call fails or times out, it logs `log.warning("could not ask the agent to resume %s: %s", ...)` and still returns the payment.

- [x] **Step 1: Write the failing tests.**
  - `test_resume_claim_refuses_a_claim_that_is_not_paused`: a waiting claim and a decided decline each get 409 with the reason.
  - `test_a_sign_off_asks_the_agent_to_resume_and_the_work_ends`: use `service_client` and the backend in process.
    - Approve 2000 on CLM-1003 (paused), then post the sign-off as Dana.
    - The thread is finished, the memory stand-in has 2 puts, and there is one payment.
  - `test_the_sign_off_stands_when_the_agent_cannot_be_reached`: the agent stand-in raises `ConnectError`.
    - The sign-off answers 200, the payment is paid, and a warning is logged.
    - The claim is still paused on the agent's side, and nothing is saved to memory.
  - `test_a_resume_runs_beside_a_dispatch`: the service's lock is held by a dispatch, and resume still answers 202.
  - `test_two_resumes_of_one_claim_pay_once`, from Review Focus 3: resume twice. The second gets 409 because the claim is not paused, and there is one payment.
- [x] **Step 2: Run them.** Expected: FAIL.
- [x] **Step 3: Implement.** Add the known gap to `docs/STATUS.md`: a sign-off the agent never heard of leaves the thread paused, and that claim is not saved to memory.
- [x] **Step 4: Run them again,** with `test_service.py`, `test_payments.py` and `test_api.py`. Expected: PASS.
- [ ] **Step 5: Check by hand.** (Done with the live checks, Task 10.) With `npm run dev` running, approve a claim over the limit; it pauses. Sign it off on the dashboard; it is paid, and the agent's log shows the resume ending the work.
- [x] **Step 6: Commit the phase:** `feat: an approval waiting for sign-off pauses, and the sign-off resumes it`.

---

## Phase 2: Sign off or send back (spec step 2)

### Task 3: `send_back_claim`, taking a sent-back claim once, and the note in the graph

**Files:**
- Modify:
  - `backend/app/db.py`: a new table, `sent_back_decisions`, with `id`, `claim_id`, the decision columns as they were (`decision`, `payout`, `calculation`, `rationale`, `citations`, `decided_by`, `manual_version`, `decided_at`), `note`, `sent_back_by` and `sent_back_at`. The `claims` table gets `resume_heard_at`.
  - New `backend/app/send_back.py`.
  - `backend/app/api.py`: `POST /api/claims/{claim_id}/send-back`, `operation_id="send_back_claim"`, body `{"note": str}`, guarded like the sign-off.
  - `backend/app/agent_routes.py`: `POST /api/agent/claims/{claim_id}/take-sent-back` (`take_sent_back_claim`) and `POST /api/agent/claims/{claim_id}/heard` (`heard_from_resume`).
  - `backend/app/runs.py`: `claims_to_dispatch`.
  - `backend/app/claims.py`: `claim_state` gains `sent_back: {"note", "sent_back_by_name", "sent_back_at"} | None`.
  - `backend/agent/graph.py`, `backend/agent/worker.py`, `backend/agent/dispatcher.py`, `backend/agent/api.py`, `backend/agent/service.py`.
  - `backend/agent/instructions_claim.md`, only where it says a decision cannot be changed.
- Test: `backend/tests/test_send_back.py`

**Interfaces:**
- **`send_back.send_back(engine, ref, claim_id, note, handler_id) -> dict`.** It returns the claim and does everything in one transaction:
  - It gates on a conditional update of the claim from `awaiting_approval` to `sent_back`, checked by row count.
  - It copies the decision row into `sent_back_decisions` with the note, who sent it back and when.
  - It deletes the decision, the held payment and the payout calculation.
  - It adds a `sent_back` step `{"note", "sent_back_by", "decision", "payout"}`.
  - It raises `NotWaiting` (409) for a claim that is not waiting and `PermissionError` (409) for an evaluation's copy. A handler who cannot sign off gets 403.
  - A note that is blank or over 1,000 characters gets 422.
- **`take_sent_back(engine, claim_id) -> bool`.** A conditional update from `sent_back` to `in_progress` that sets `resume_heard_at=now()`. True only for the first caller.
- **`heard_from_resume(engine, claim_id)`** sets `resume_heard_at=now()`.
- **`claims_to_dispatch`:**
  - It adds `sent_back` claims after the in-progress ones.
  - It leaves out a claim `in_progress` whose `resume_heard_at` is within 300 s.
- **The agent:**
  - `resume` and the dispatcher's `work` call `api.take_sent_back(claim_id)` for a `sent_back` claim. A claim already taken is refused (`"<claim> is already being worked"`).
  - While resuming, a thread posts `heard` every 60 s.
  - The node `wait_for_sign_off`, seeing the claim sent back, returns `Command(goto="model", update={"proposal": None, "decision": None, "model_calls": 0, "messages": [HumanMessage(send_back_message(note, name))]})`.
- **`agent/prompts.py: send_back_message(note: str, handler_name: str) -> str`** is exact copy, kept in code, not in the instructions:

  > "{handler_name}, a senior claims handler, sent your decision back for review with this note: \"{note}\". Treat it as a senior handler's review of the decision: check what it asks against the policy wording and the claims manual like everything else. If the wording does not support what the note asks, refer the claim to a handler and give the reason. Record a new decision."

- [x] **Step 1: Write the failing tests.**
  - `test_send_back_refusals`, parametrized:
    - a blank note gets 422;
    - a note of 1,001 characters gets 422;
    - a claim not waiting gets 409;
    - an evaluation's copy gets 409;
    - Marcus gets 403.
  - `test_a_sign_off_and_a_send_back_at_the_same_moment_only_one_wins`: two threads with a barrier. The status codes, sorted, are `[200, 409]`, and the payments and decisions agree with whichever won.
  - `test_the_decision_is_kept_with_its_note`: `sent_back_decisions` has the row with the note, Dana and a time. `decisions` has no row, the payment is gone, and the step is recorded.
  - `test_a_sent_back_claim_is_taken_once`: `take_sent_back` twice gives `[True, False]`.
  - `test_a_dispatch_started_during_a_resume_leaves_its_claim_alone`: set `resume_heard_at` to now; `claims_to_dispatch` leaves the claim out. Six minutes later it puts it back in.
  - `test_the_agent_decides_again_and_a_new_approval_within_the_limit_is_paid`: approve 2000 (paused), send back "Check the per-item limit", then resume. The stub approves 900. The claim is paid, the model saw the message with the note and Dana's name, and `model_calls` started again from 0.
  - `test_a_new_approval_over_the_limit_waits_for_sign_off_again`
  - `test_a_send_back_the_agent_never_received_is_worked_by_the_next_dispatch`: send back with the agent's call failing, then `dispatch(env, run_id, 1)`. The claim is decided again.
  - `test_a_claim_can_be_sent_back_twice`
  - `test_the_note_is_in_code_not_in_the_versioned_instructions`
- [x] **Step 2: Run them.** Expected: FAIL.
- [x] **Step 3: Implement.** In `instructions_claim.md`, change only the sentence that says a decision cannot be changed: a decision can be sent back by a senior handler.
- [x] **Step 4: Run them again,** with the tests of the graph, the worker, the dispatcher, the API and payments. Expected: PASS.

### Task 4: The dashboard: Sign off and Send back; sent-back decisions on the claim page

**Files:**
- Modify:
  - `frontend/src/api.ts`: `Status` gains `'sent_back'`; `sendBack(claimId, note)`; `ClaimDetailData.sent_back: SentBack[]`; `signOff(paymentId)` already exists.
  - `frontend/src/format.ts`: `STATUS_LABEL.sent_back = 'Sent back'`.
  - `frontend/src/pages/ClaimDetail.tsx`: the held notice becomes a panel with "Sign off" and "Send back". Send back opens a note box: required, 1,000 characters at most, with a counter.
  - `frontend/src/pages/Trace.tsx`: a `sent_back` step.
  - `frontend/src/pages/Approvals.tsx`: a "Review on the claim page" link.
  - `backend/app/api.py`: `GET /claims/{id}` adds `sent_back: [...]`, newest first, and `waiting_for_next_dispatch: bool`, which is True when the status is `sent_back`.
- Test: `frontend/src/pages/ClaimDetail.test.tsx`, `frontend/src/pages/Queue.test.tsx`, `frontend/src/pages/TraceSteps.test.tsx`, `backend/tests/test_send_back.py`

- [x] **Step 1: Write the failing tests.**
  - `a senior handler sees Sign off and Send back on a claim waiting for sign-off; Marcus sees neither`
  - `Send back needs a note and posts it`: the button is disabled while the note is blank; typing 1,001 characters shows "1,000 characters at most"; the POST body is `{note}`.
  - `the claim page shows each sent-back decision with its note, who and when, beside the current one`
  - `a sent-back claim says it is waiting for the next dispatch`
  - In Queue: `shows a sent-back claim with its status`.
  - In Trace: `a sent_back step shows the note`.
  - In the backend: `test_claim_detail_lists_sent_back_decisions_newest_first`.
- [x] **Step 2: Run them, implement, run them again.** Run `npx vitest run src/pages`, `npx tsc -b` and `npx oxlint --deny-warnings src`. Expected: PASS and clean.
- [x] **Step 3: Run the whole backend suite and the e2e.** Then commit the phase: `feat: a senior handler signs off or sends back a large approval`.

---

## Phase 3: The agent as an AgentCore app (spec step 3)

### Task 5: The agent imports nothing from the app

**Files:**
- Create: `backend/agent/settings.py`
- Modify:
  - `backend/agent/checkpoints.py`: its own `SCHEMA`, `CONNECTION_WAIT_S` and `row_level_security`, with the same values; `prepare_threshold=None` in both places.
  - `backend/agent/worker.py`: no `Reference`; `build_claim_graph` loses its unused `ref`.
  - `backend/agent/graph.py`, `backend/agent/models.py`, `backend/agent/__main__.py`, `backend/agent/service.py`.
  - `backend/agent/connection.py`: `backend_url()` reads `UNDERCOVER_API`, then `UNDERCOVER_BACKEND`.
  - `backend/tests/fakes.py`: `agent_env` builds `AgentSettings` for the agent and `Settings` for the backend.
- Test: `backend/tests/test_agent_boundaries.py`

**Interfaces:**
- **`agent.settings.AgentSettings(BaseSettings)`** has exactly the fields the agent reads today:
  - `openrouter_api_key`, `agent_model`, `agent_temperature`, `agent_max_attempts`, `agent_step_retries`, `agent_claim_timeout_s`, `agent_max_model_calls`, `agent_max_tokens`, `agent_provider`;
  - `db_path`, `database_url` (env only), `api_key` (env only), `model_name`;
  - the properties `reasoning_effort` and `screening_model`.

  It has the same env names, `agent.toml` table and defaults as `app.config.Settings`, and it reads `.env` the same way.
- **`agent.settings.key_matches(header_value: str | None, key: str | None) -> bool`**, using `secrets.compare_digest`.

- [x] **Step 1: Write the failing tests.**
  - `test_no_agent_module_imports_app`: walk `agent/*.py` with `ast`; no import starts with `app`.
  - `test_the_agent_package_imports_on_its_own`: copy `backend/agent` into a temp dir with nothing else, then run `python -c "import agent.entrypoint, agent.worker"` with `cwd` set to the temp dir. It exits 0. (`agent.entrypoint` exists from Task 6, so mark this test `xfail` until then.)
  - `test_the_checkpoint_pool_prepares_nothing`: `make_checkpoints` on a fake `ConnectionPool` sees `kwargs["prepare_threshold"] is None`.
  - `test_the_backend_address_reads_UNDERCOVER_API_first`
- [x] **Step 2: Run them, implement, run the whole backend suite.** Expected: PASS.

### Task 6: `agent/entrypoint.py`, the AgentCore app

**Files:**
- Create: `backend/agent/entrypoint.py`, `backend/agent/operations_in.py` (the payload models)
- Delete: `backend/agent/service.py`. Its tests move to `backend/tests/test_entrypoint.py`, and `ServiceClient` in `fakes.py` becomes `agent_app_client` over Starlette's `TestClient`.
- Modify:
  - `backend/pyproject.toml` and `uv.lock`: `bedrock-agentcore` in the dependencies; a `runtime` group with exactly what the agent imports (`bedrock-agentcore`, `langgraph`, `langgraph-checkpoint-postgres`, `langgraph-checkpoint-aws`, `langchain-core`, `langchain-openai`, `langfuse`, `psycopg[binary,pool]`, `httpx`, `pydantic-settings`, `python-dotenv`, `mcp<2`, `boto3`, `openrouter`, `pyyaml`); `boto3` in the `server` group.
  - `scripts/check.mjs`: a step, "Agent imports on Runtime", that copies `runtime/` and `backend/agent/` (without `tests`) into a temp dir, as the stack does, and runs `uv run --project backend --isolated --only-group runtime python -c "__import__('runtime.main')"` with the temp dir as `cwd`.
  - `scripts/dev.mjs`: the agent part becomes `python -m uvicorn agent.entrypoint:app --host 127.0.0.1 --port 8080 --reload --reload-dir agent`, in place of 8002.
- Test: `backend/tests/test_entrypoint.py`

**Interfaces:**
- **The app:** `app = BedrockAgentCoreApp()` and one `@app.entrypoint def invoke(payload: dict, context) -> dict`.
- **Payload models** (`operations_in.py`, pydantic, discriminated on `operation`):
  - `Health`;
  - `StartDispatch(workers: int 1..25)`;
  - `StartEvaluation(eval_set, tiny, workers)`;
  - `StartImproverRound(handler_id)`;
  - `DecideRoundProposal(round_id, proposal_id, approve, reason, handler_id)`;
  - `ResumeClaim(claim_id)`.

  A `ValidationError` answers `HTTPException(400, "<operation>: <field>: <message>")`. An unknown operation answers `HTTPException(400, "unknown operation <name>")`.
- **Getting ready:** `get_ready() -> AgentEnv` runs once per process, behind a lock, and only on a request; importing opens nothing.
  - On Runtime (when any `AGENTCORE_CREDENTIAL_<NAME>_NAME` is set) it calls `random.seed()` and, for each such setting, `requires_api_key(provider_name=os.environ[setting])(lambda *, api_key: api_key)()`, putting the value into `os.environ[NAME]`.
  - It then checks `UNDERCOVER_API_KEY`, opens `open_env(AgentSettings())` in a module-level `ExitStack`, and marks stopped the runs and rounds left running, as the service's lifespan does today.
  - On failure it raises, so that request answers 500 with the error, and the next request tries again.
- **`on_runtime() -> bool`:** any env name matching `AGENTCORE_CREDENTIAL_.*_NAME`.
- **The key:** off Runtime, every operation but `health` needs `context.request_headers["Authorization"] == f"Bearer {key}"` (via `key_matches`); otherwise it answers `HTTPException(401, "this needs the agent's API key")`.
- **The work:** `run_in_background(name, fn)` calls `task = app.add_async_task(name)` before answering, then starts a thread whose `finally` calls `app.complete_async_task(task)`.
- **Answers:** a refusal from the service's old 409 paths answers `{"refused": reason}`; accepted work answers `{"accepted": True}`.

- [x] **Step 1: Write the failing tests** in `test_entrypoint.py`, using `TestClient(app)` with stand-ins for the work (monkeypatch `dispatch`, `improver.work_round` and `worker.resume`) and for Identity (monkeypatch `requires_api_key`).
  - `test_importing_the_agent_opens_nothing`: in a subprocess, import `agent.entrypoint` with `DATABASE_URL` pointing at an unreachable host; it exits 0 quickly.
  - `test_on_runtime_the_first_request_fetches_each_credential_once`: set `AGENTCORE_CREDENTIAL_OPENROUTER_API_KEY_NAME=prov`. After two requests the fake was called once per credential and `os.environ["OPENROUTER_API_KEY"]` is set.
  - `test_getting_ready_that_fails_answers_the_error_and_the_next_request_tries_again`
  - `test_each_operation_accepts_work`, parametrized over the five working operations: `{"accepted": True}`.
  - `test_a_dispatch_while_one_runs_is_refused_with_the_reason`
  - `test_a_payload_with_an_input_missing_is_a_400_that_says_which`, and `test_an_unknown_operation_is_a_400`.
  - `test_ping_is_busy_while_work_runs_and_healthy_after_it_finishes_or_fails`: an `Event`-gated stand-in shows `/ping` as `HealthyBusy`; after it finishes, `Healthy`; the same after an exception.
  - `test_off_runtime_a_request_without_the_key_is_refused_but_health_is_not`
  - `test_on_runtime_no_key_is_asked_for`
  - `test_health_answers_healthy_once_ready`
- [x] **Step 2: Run them.** Expected: FAIL.
- [x] **Step 3: Implement.** Run `uv add bedrock-agentcore`, edit the groups, then run `uv lock`.
- [x] **Step 4: Run them again; then flip Task 5's `xfail`; then run the check steps:** `node scripts/check.mjs` up to "Agent imports on Runtime". Expected: PASS.

### Task 7: The app's two routes to the agent

**Files:**
- Modify:
  - `backend/app/agent_client.py`: rewritten.
  - `backend/app/config.py`: `agent_url` defaults to `http://127.0.0.1:8080`; `agent_runtime_arn` comes from `UNDERCOVER_AGENT_RUNTIME`, env only.
  - `backend/app/api.py`: `forward_run` becomes `ask_agent(payload)`.
  - `backend/tests/conftest.py`: `UNDERCOVER_AGENT_RUNTIME` in `OUTSIDE_WORLD`.
  - `scripts/check.mjs`: clear it.
- Test: `backend/tests/test_agent_client.py`

**Interfaces:**
- **`agent_client.send(settings, payload: dict, http=httpx, boto=None) -> dict`** returns the agent's JSON when it answers `accepted` or `healthy`. Otherwise it raises:
  - `AgentRefused(reason)`: the agent answered `{"refused": ...}`. The app maps it to 409 with the reason.
  - `AgentNotReached(reason)`: the request was never sent or never connected, or the agent sent an error answer (`httpx.ConnectError` or `ConnectTimeout`, an HTTP status ≥ 400; `botocore.exceptions.ConnectionError` or `ClientError`). The app maps it to 503 `"the agent is not running"`, and the caller undoes (Task 9).
  - `AgentMaybe(reason)`: the request was sent and something failed after (`httpx.ReadTimeout` or `RemoteProtocolError`; botocore `HTTPClientError`, `ReadTimeoutError` or `IncompleteReadError`). The app maps it to 504 `"The agent did not answer in time. Reload the page to see whether it went through."` and changes nothing.
- **The HTTP route:** `POST {agent_url}/invocations` with `Authorization: Bearer <key>` and `timeout=10`.
- **The Runtime route:** `boto3.client("bedrock-agentcore", region_name=<session region>, config=Config(retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=20)).invoke_agent_runtime(agentRuntimeArn=arn, runtimeSessionId=uuid4().hex + uuid4().hex[:8], payload=json.dumps(payload))`, with no key. It reads `json.loads(response["response"].read())`.
- **`resume_claim(settings, claim_id)`** is `send(settings, {"operation": "resume_claim", "claim_id": claim_id})` with every exception logged and swallowed. The sign-off and the send-back use it.

- [x] **Step 1: Write the failing tests** with an `httpx.MockTransport` and a fake boto client.
  - `test_the_laptop_route_sends_the_key_and_the_payload_to_invocations`
  - `test_the_runtime_route_sends_a_new_session_id_of_33_characters_or_more_and_no_key`: two calls get different ids.
  - `test_an_acceptance_on_each_route`
  - `test_a_refusal_is_passed_on`
  - `test_a_failed_connection_is_not_reached`
  - `test_an_error_answer_is_not_reached`
  - `test_a_timeout_or_a_cut_off_answer_is_maybe`
  - In `test_api.py`: `test_a_dispatch_the_agent_refused_is_a_409_and_a_timeout_says_to_reload`.
- [x] **Step 2: Run them, implement, run them again, then the whole backend suite.**
- [x] **Step 3: Move the e2e's agent stand-in.** In `e2e/stand_in_agent.py`, `/invocations` answers the payloads and `/ping` answers `{"status": "Healthy"}`. `playwright.config.ts` waits on `/ping` and sets `UNDERCOVER_AGENT=http://127.0.0.1:8003` and `UNDERCOVER_AGENT_RUNTIME=""` for the backend.
- [x] **Step 4: Run the e2e, and check by hand.** Run `cd e2e; npm test`. Then, with `npm run dev` stopped, run `PYTHONPATH=backend uv run --project backend --env-file .env --isolated --only-group runtime python -m runtime.main` from the repository root, and post `{"operation":"health"}` to `http://127.0.0.1:8080/invocations` with the key. Expected: `{"healthy": true}`.
- [x] **Step 5: Commit the phase:** `feat: the agent is an AgentCore app, on the laptop and on Runtime`.

---

## Phase 4: The app keeps one run at a time (spec step 4)

### Task 8: The app creates runs and rounds and records reviews, under one lock

**Files:**
- Create: `backend/app/work.py`
- Modify:
  - `backend/app/db.py`: a new `work_lock` table, one row with `id=1` and `taken_at`, seeded; `runs.heard_at`; `rounds.heard_at`; `proposals.previous_recipe`.
  - `backend/app/api.py`: the start routes, `decide_proposal_route`, the stop route.
  - `backend/app/improvement.py`: `decide_proposal` records `previous_recipe`; new `clear_review(engine, proposal_id)`.
  - `backend/app/runs.py`: new `stop_run(engine, run_id, reason)`.
  - `backend/app/agent_routes.py`: removes `POST /dispatches`, `/evaluations` and `/rounds` (the agent no longer creates runs or rounds, apart from a round's own evaluations, which stay), `POST /rounds/{id}/decision` and `POST /runs/stop-running`; adds `POST /runs/{id}/heard` and `POST /rounds/{id}/heard`.
- Test: `backend/tests/test_one_at_a_time.py`

**Interfaces:**
- **`work.start_work(engine, settings, kind: Literal["dispatch", "evaluation", "round", "review"], create: Callable[[Connection], int]) -> int`.** In one short transaction:
  1. It takes the lock with `UPDATE work_lock SET taken_at = now() WHERE id = 1`.
  2. It stops stale work: a running run, or an open round in a working stage, whose `max(started_at or reviewed_at, heard_at)` is older than 300 s. A stale run is stopped with `error = "The agent stopped answering"`. A stale round is stopped with that reason, and so is its running evaluation.
  3. It refuses with `WorkGoing(what)` while a running run, or an open round with no waiting proposal and a working stage, remains. `what` is `"a dispatch"`, `"an evaluation"` or `"round N"`.
  4. It calls `create(conn)`, then commits.

  The app maps `WorkGoing` to 409 `"<what> is already going"`. `resume_claim` never calls it.
- **The app's calls to the agent:**
  - `{"operation": "work_run", "run_id": ...}` for a dispatch or an evaluation;
  - `{"operation": "work_round", "round_id": ...}`;
  - `{"operation": "carry_round_on", "round_id": ..., "proposal_id": ...}`.
- **When the agent did not take the work** (`AgentNotReached` or `AgentRefused`):
  - a run: `stop_run(engine, id, reason)`;
  - a round: `stop_round_row(engine, id, None)` with `error=reason`;
  - a review: `clear_review(engine, proposal_id)`.

  `AgentMaybe` changes nothing.
- **`clear_review(engine, proposal_id)`:**
  - The proposal goes back to `waiting`, with `decided_by`, `decided_at` and `decision_reason` cleared.
  - An approved change: the version it created is deleted, and `rounds.version_tried` and `reason` are cleared.
  - An approved recipe: the version it created is deleted, and `previous_recipe` becomes current again.

- [x] **Step 1: Write the failing tests.**
  - `test_a_second_start_is_refused_while_a_dispatch_an_evaluation_or_a_working_round_is_going`: parametrized. The 409 names what is going. A round that is waiting for a review does not block.
  - `test_two_starts_at_the_same_moment_only_one_goes_ahead`: two threads with a barrier, on a file SQLite. Exactly one run is created.
  - `test_a_start_after_five_quiet_minutes_stops_the_old_work_with_its_reason`: set `started_at` back 6 minutes. The old run is stopped with `"The agent stopped answering"`; a round's evaluation is stopped with its round.
  - `test_a_start_the_agent_did_not_take_is_stopped_with_the_reason`
  - `test_a_timeout_leaves_the_work_going`
  - `test_a_review_the_agent_did_not_take_is_cleared`: both the change and the recipe cases; the version is gone and the earlier recipe is current.
  - `test_a_request_uses_one_connection_at_a_time`: on a pool of one with `pool_timeout=1`, as in `test_section14`.
  - `test_a_resume_starts_beside_a_dispatch`
- [x] **Step 2: Run them, implement, run them again.**

### Task 9: The agent works what it is given, reports every minute, and stops only its own work

**Files:**
- Modify:
  - `backend/agent/entrypoint.py`: the operations become `health`, `work_run`, `work_round`, `carry_round_on` and `resume_claim`. The in-process one-at-a-time lock and the stop-everything step of getting ready go.
  - `backend/agent/dispatcher.py`: `dispatch(env, run_id)` reads `workers` from the run.
  - `backend/agent/improver.py`: `work_round(env, round_id)`; `carry_on(env, round_id, proposal_id)` reads the review from `improver_api.get_proposal`; `record_decision` and `begin` go.
  - `backend/agent/api.py` and `improver_api.py`: `heard_run(run_id)`, `heard_round(round_id)`, `stop_run(run_id, reason)` (the app's `finish_run` with an error), and `stop_round(round_id, reason)`, which also stops its running evaluation.
  - New `backend/agent/heartbeat.py`: `heartbeat(send: Callable[[], None], every_s: float = 60) -> ContextManager`.
  - `e2e/stand_in_agent.py`: given the app's address and the key, it finishes each run and round it gets, so later starts are not refused.
  - `frontend/src/pages/Scoreboard.tsx`, `Improvement.tsx` and `Queue.tsx`: a stopped run or round shows its reason.
- Test: `backend/tests/test_entrypoint.py`, `backend/tests/test_heartbeat.py`, the e2e specs

**Interfaces:**
- **Refusals:**
  - `work_run` refuses a run that is not `running` (`"run N is not running"`).
  - `work_round` and `carry_round_on` refuse a round that is not open, and `carry_round_on` also refuses a proposal with no review.
- **On failure:** a run's work that fails calls `stop_run(run_id, "<Error>: <message>")`, and a round's calls `stop_round(round_id, ...)`. Nothing else is stopped.
- **The heartbeat:** a daemon thread calls `send()` every `every_s` seconds until the block exits, and logs and ignores any error. It is used around a run's work, a round's work (whose word covers its evaluation), and a resume.

- [x] **Step 1: Write the failing tests.**
  - `test_work_run_refuses_a_run_that_is_not_running`
  - `test_a_failed_run_stops_only_itself`: start a run, then fail it; another round's evaluation stays running.
  - `test_stopping_a_round_stops_its_running_evaluation`
  - `test_the_heartbeat_reports_every_interval_and_stops_with_the_block`: with `every_s=0.01`, there are at least 3 calls, and none once the block has exited.
  - `test_carry_round_on_reads_the_review_from_the_app`
  - In the frontend: `a stopped run shows its reason`.
- [x] **Step 2: Run them, implement, run them again.** Then run the whole backend suite, the frontend checks and the e2e (`cd e2e; npm test`). Expected: all PASS, with the app creating runs, rounds and reviews.
- [ ] **Step 3: Check by hand.** (Done with the live checks, Task 10.) With `npm run dev` running, start a dispatch at N=25; a second "Start dispatch" is refused on the dashboard with "a dispatch is already going".
- [x] **Step 4: Commit the phase:** `feat: the app keeps one piece of the agent's work going at a time`.

---

## Docs and live checks

### Task 10: Docs, the clean check, then the live runs

**Files:**
- Modify:
  - `docs/SPEC.md`: the send-back, the sent-back status, and the agent's operations.
  - `docs/TECH_STACK.md`: the Deployment section gets the agent on Runtime, as the spec lists it, plus the packaging mismatch from the rulings.
  - `docs/PROCESS.md`: `npm run dev` serves the agent on 8080; how to stop Lab 1's `agentcore dev` first; the health check through `runtime/main.py`.
  - `docs/CODE_STANDARDS.md`: the agent imports nothing from `app`; the entrypoint; the app keeps one piece of work at a time.
  - `docs/STATUS.md`.
  - Move this plan to `docs/plans/` when it is ticked off.

- [ ] **Step 1: Write the docs.** Then run `npm run check` piecewise with `.env` moved away (backend, the four import checks, vitest, tsc, lint, e2e). Expected: green.
- [ ] **Step 2: Run the live checks.** Run `npm run seed`, then `npm run dev` on the default model, on the direct route, with memory off.
  - Dispatch at N=25 and follow the run in the database until it finishes.
  - Signed in as Dana by a script (the password never passes through a tool call), send one claim over the limit back with a note. The agent decides it again; check its page shows both decisions and the note.
  - Sign another off; it is paid and its work ends.
  - Evaluate the evaluation set's five tiny claims.
  - Check the claim pages, and report what the runs spent from `model_calls` in `docs/STATUS.md`.
- [ ] **Step 3: Check the health on the laptop.** Stop `npm run dev`, run the `runtime.main` health check from Task 7 Step 4, and record its answer.
- [ ] **Step 4: Commit:** `docs: the send back and the agent on AgentCore Runtime`.
