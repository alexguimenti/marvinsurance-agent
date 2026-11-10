# Security changes before UnderCover goes on AWS: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Tick them off here.

**Goal:** The agent reaches the backend only over HTTP with an API key, a second model screens each claim for hidden instructions, the backend publishes the agent's own API and pays at sign-off, and the app is ready to run on Lambda or a plain web server.

**Architecture:** All backend routes move under `/api`. `agent/connection.py` is the one HTTP client; `agent/api.py` (claim agent, worker, dispatcher, service) and `agent/improver_api.py` (improver) are the only modules that call it. `backend/app/` owns every record. The agent keeps one direct database link: LangGraph's checkpoints. The screen is the claim graph's first node, outside the versioned configuration. `backend/main.py` is the deployment's entry point, and the backend serves the dashboard's build when it exists.

**Tech Stack:** FastAPI, httpx, SQLAlchemy Core (`NullPool` on Lambda), psycopg, LangGraph, LangChain Structured Outputs, Mangum, Vite, uv dependency groups, Playwright.

**Spec:** `docs/SECURE.md`. Read it alongside this plan. Where they disagree, the spec wins unless a line under "Decisions" says otherwise.

**Sidenote from the brief:** For consistency with the course, please read `labs/week3day2_lab1.ipynb` and build the check in step 2 in the same style as the lab, and read `labs/week3day2_lab2.ipynb`, which deploys the app, without running it. Where the lab and this document differ, follow this document. In this repo the two labs are in the course's copy, `../undercover/labs/`; `labs/week3day2_mex.ipynb` is our earlier exercise, with a different screener.

**Before this plan starts:** `docs/PLAN_IMPROVE.md` Task 10 and its final review are finished and recorded in `docs/STATUS.md`, by the owner's decision, because the `npm run seed` in Phase 5 deletes the rounds.

## Decisions

The brief's notes come from another build. The owner chose to move the agent onto HTTP, reversing the in-process decision in `PLAN_DURABLE.md`.

| The brief says | This repo | Decision |
|---|---|---|
| `agent/connection.py`, `agent/api.py`, `agent/improver_api.py` | The agent calls `app` in Python | Create all three and move every record read and write of the agent onto them |
| Routes are at `/api/...` | Routes have no prefix; Vite strips `/api` | Every backend route goes on an `APIRouter(prefix="/api")`. The Vite rewrite goes. The agent's service keeps its unprefixed routes |
| `backend/main.py`, `backend.main.handler`, `backend.main:app` | `backend/app/main.py`, package `app` | Add `backend/__init__.py` and a small `backend/main.py` that puts `backend/` on `sys.path`, imports `app.main.app` and defines `handler`, so the deployment's names work. `app.main:app` stays for `npm run dev` |
| `dashboard/dist` | Source in `frontend/` | Keep `frontend/`. `npm run build` writes to `dashboard/dist`. `/dashboard/` goes in `.gitignore` |
| Root `pyproject.toml` with a `server` group already added | `backend/pyproject.toml`, no group | Add `[dependency-groups] server` to `backend/pyproject.toml`. The deployment runs `uv export` with `--project backend` |
| Tools `get_claim_history`, `record_decision`, `pay_claim` | Tools `get_customer_history`, `save_decision`; paying is a graph step | Tool names stay. Only the HTTP operations take the brief's names |
| Exactly eight agent operations | The agent also needs a plan, section lists, public facts and record keeping | Those are key-only routes under `/api/agent/`, left out of the document. The document is selected by the set `GATEWAY_OPERATIONS` |
| `dashboard` route `get_claim` | Same `operation_id` and path | `GET /api/claims/{claim_id}` stays one shared route. The agent's client reads its `claim` field |
| A claim's "section" | `decisions.citations` | The security check records `citations: ["Security check"]` |
| CLM-1901 read "beside `data/claims.json`" | `Reference` refuses `security/` | `Reference` merges `security/claims.json`, `policies.json` and `customers.json`. `security/decisions.json` is never read. `tests/test_reference.py` and `test_evals_isolation.py` are updated to say exactly this |
| The check on GPT-6 Luna | The owner kept DeepSeek for the agents | The check follows the brief: `openai/gpt-6-luna`, effort `none`. The claim agent stays on DeepSeek. The live check confirms Luna answers through our key |
| Product name "UnderCover" in the lab's instructions | Standards name the product MarvInsurance | Replace the name in the instructions. Nothing else in them changes |

These follow from the brief and the code:

1. `UNDERCOVER_API_KEY` is read only from the process environment, in `model_post_init`, as `DATABASE_URL` is. A new `UNDERCOVER_BACKEND` (default `http://127.0.0.1:8000`) tells the agent where the backend is.
2. The agent still holds `DATABASE_URL` for its checkpoints. The API key does not remove that access. Taking it away is part of the move to the Gateway.
3. The dashboard's `signed_off` payment status goes. A sign-off goes from `held` to `paid` in one conditional `UPDATE`, then sets the claim `decided`, and adds the `paid` step. `sweep_signed_off`, `signed_off_claims`, `resume`, `POST /claims/{id}/resume` and `notify_sign_off` are deleted.
4. The backend grades evaluations: `finish_run` runs there, and the agent's HTTP timeout is 30 seconds.
5. Only the backend seeds. `prepare_database` is deleted; the service waits up to 60 seconds for `GET /api/health` when it starts, since `npm run dev` starts everything at once.
6. An httpx transport error joins `RETRYABLE`, so a backend restart carries the claim on rather than failing an attempt.
7. Tests keep `env.engine`: `fakes.agent_env` sets it on the yielded env for inspection. `AgentEnv` itself has no engine.

## Global Constraints

- "The key is compared in constant time, with Python's `secrets.compare_digest`."
- "The key never stands in for a handler."
- "`GET /api/health` stays open."
- "When `UNDERCOVER_API_KEY` isn't set, the backend still starts and handlers can still use the dashboard, but every request that needs the key is refused. The agent's service refuses to start without the key and says it's missing."
- Key checks add no inputs to any operation.
- The check: `openai/gpt-6-luna`, effort `none`; with `MODEL_NAME` set, that model with no effort, and `MODEL_NAME` read in one place. At most three attempts on an unreadable answer.
- "At run time the backend imports nothing from outside the package (the agent's code included), reads files only from the package, and writes no files."
- Every operation has an `operation_id`, a one-line docstring and no optional inputs.
- "Tests never call a model, Supabase Auth or AWS, `npm run check` never reads `.env`."
- "Everything `npm run dev` starts, `npm run build` and `npm run check` work on Windows too."
- "Nothing in this change touches AWS."
- `.env` is never read or printed by the agent working this plan; check names only. Commit at the end of each phase. Live checks last, after one `npm run seed`.

## Review Focus

1. A malformed or non-ASCII `X-API-Key`, or an empty configured key: a 401, never a 500 and never a match. Test in Task 2.
2. The approval limit is lowered after a decision was recorded under a higher one, so `needs_sign_off` says no and `pay_claim` answers 403: the payout is held for a handler, and the claim doesn't fail. Test in Task 7.
3. Lambda's transaction pooler doesn't keep `SET search_path` between statements: on Lambda every statement names the schema. Test in Task 9, live check in Task 12.
4. A path such as `/..%2F..%2F.env` or `/assets/../../data/claims.json` on the dashboard route: only files inside the build are served. Test in Task 8.
5. The backend restarts while claims are being worked: no attempt is used up and the claim carries on from its last step. Test in Task 4.

---

## File structure

```
backend/
  main.py, __init__.py        new: the deployment's app and handler
  app/api.py                  /api router, guards, agent routes, dashboard files, the agent's OpenAPI document
  app/agent_routes.py         new: the key-only /api/agent routes (api.py is already 400 lines)
  app/auth.py                 agent_key, key_or_handler
  app/config.py               api key, backend url, model override, on_lambda, dashboard_dir, screening_model
  app/db.py                   NullPool, prepare_threshold=None and schema_translate_map on Lambda
  app/claims.py, runs.py      the conditional updates the worker did itself (take_claim, fail_claim, carry_on_claim, claim_state)
  app/payments.py             sign_off pays; pay_claim checks limit then amount
  app/reference.py, seed.py   CLM-1901
  app/configs.py              first instructions and recipe read without importing agent
  agent/connection.py         new: the one httpx client
  agent/api.py                new: the claim agent's client
  agent/improver_api.py       new, replaces improver_backend.py
  agent/screening.py          new: the check
  agent/graph.py, tools.py, worker.py, dispatcher.py, service.py, improver.py, __main__.py, scripted.py
scripts/build.mjs             new
scripts/check.mjs, dev.mjs, package.json, e2e/*, frontend/vite.config.ts, frontend/src/pages/ClaimDetail.tsx
```

---

## Phase 1: An API key for the agent

### Task 1: Everything under `/api`, and an open health check

**Files:** Modify `backend/app/api.py`, `frontend/vite.config.ts`, `e2e/playwright.config.ts`, `backend/tests/*` (client fixtures), `scripts/check_live.py`. Test: `backend/tests/test_api.py`.

**Interfaces:** Produces `GET /api/health` (`operation_id="get_health"`) returning `{"status": "ok"}`. Tests reach the prefix with `TestClient(app, base_url="http://testserver/api")`, so their relative paths don't change.

- [x] **Step 1: Write the failing test** `test_health_is_open_and_routes_live_under_api`: `/api/health` is 200 with no credentials and `/claims` is 404.
- [x] **Step 2: Run it.** `cd backend; uv run pytest tests/test_api.py -k health -q`. Expected: FAIL, 404 on `/api/health`.
- [x] **Step 3: Implement.** In `create_app`, `router = APIRouter(prefix="/api")`, replace every `@app.` route decorator with `@router.`, end with `app.include_router(router)`. `FastAPI(..., docs_url=None, redoc_url=None, openapi_url=None)`: a public address publishes no schema. Remove the Vite `rewrite`. Point Playwright's backend `url` at `/api/health`. Add `base_url` to each test client.
- [x] **Step 4: Run.** `uv run pytest -q` and `npx vitest run` in `frontend`. Expected: all pass.

### Task 2: The key and the guards

**Files:** Modify `backend/app/config.py`, `backend/app/auth.py`, `backend/app/api.py`, `backend/agent/service.py`, `backend/app/agent_client.py`, `backend/tests/conftest.py`, `scripts/check.mjs`. Test: `backend/tests/test_api_key.py`.

**Interfaces:** Produces `Settings.api_key: str | None`; `make_guards(ref, jwks, api_key) -> Guards` with fields `signed_in`, `senior`, `agent_key`, `key_or_handler`; `agent_client.start(http, url, path, body, key)`.

Which guard each route uses:

| Guard | Routes |
|---|---|
| None | `GET /api/health` |
| `key_or_handler` | The eight operations' reads and `calculate_payout`: `get_claim`, `get_policy`, `get_claim_history`, `search_documents`, `read_section`, `calculate_payout`; `GET /api/agent/openapi.json` |
| `agent_key` | `record_decision`, `pay_claim`, everything else under `/api/agent/` |
| `signed_in` or `senior` | Every other route, reads included |

- [x] **Step 1: Write the failing tests:**

```python
def test_an_agent_route_refuses_no_key_and_a_wrong_key_and_takes_the_right_one(client): ...  # POST /agent/claims/CLM-1001/steps: 401, 401, 201
def test_a_shared_read_takes_the_key_or_a_sign_in_and_refuses_neither(client): ...           # GET /claims/CLM-1001: 200, 200, 401
def test_a_handler_action_refuses_only_the_key(client): ...                                  # dispatch, evaluation, round, sign-off, proposal, setting: 401
def test_a_dashboard_read_refuses_only_the_key(client): ...                                  # GET /claims, /evaluations, /approvals: 401
def test_with_no_key_set_handlers_work_and_every_key_route_is_refused(client_no_key): ...
@pytest.mark.parametrize("bad", ["", " ", "é", "x" * 10_000])
def test_a_malformed_key_is_401_never_500(client, bad): ...
def test_the_backend_sends_the_key_to_the_service(spy_http): ...
def test_the_service_refuses_a_call_without_the_key(service): ...  # 401, /health stays open
def test_the_service_refuses_to_start_without_the_key(settings, monkeypatch): ...  # raises, message names UNDERCOVER_API_KEY
```

- [x] **Step 2: Run.** `uv run pytest tests/test_api_key.py -q`. Expected: FAIL.
- [x] **Step 3: Implement.** Guards read `request.headers` directly, never `Header()` or a security class, so operations gain no input. Compare UTF-8 bytes with `secrets.compare_digest`, after refusing a missing or empty configured key or header. `conftest.py` sets a fixed key with an autouse fixture and adds the name to `OUTSIDE_WORLD`; `check.mjs` deletes it from the environment. Replace `test_reads_stay_open`. Give each test its credentials.
- [x] **Step 4: Run.** `uv run pytest -q`. Expected: all pass.

### Task 3: The claim agent over HTTP

**Files:** Create `backend/agent/connection.py`, `backend/agent/api.py`, `backend/app/agent_routes.py`. Modify `backend/app/api.py`, `claims.py`, `runs.py`, `backend/agent/tools.py`, `graph.py`, `worker.py`, `backend/tests/fakes.py`. Test: `backend/tests/test_agent_api.py`.

**Interfaces:**

- `connection.call(method: str, path: str, json: dict | None = None, params: dict | None = None) -> Any`: reads `UNDERCOVER_API_KEY` and `UNDERCOVER_BACKEND` from `os.environ` as each request is sent, timeout 30 s; maps 404 to `LookupError`, other 4xx to `Refused(status_code, detail)`. `connection.use(client) -> ContextManager`: a test's client.
- The eight operations:

| operation_id | Route | Input |
|---|---|---|
| `get_claim` | `GET /api/claims/{claim_id}` | path |
| `get_policy` | `GET /api/claims/{claim_id}/policy` | path |
| `get_claim_history` | `GET /api/claims/{claim_id}/history` | path |
| `search_documents` | `GET /api/documents/search` | required `query` |
| `read_section` | `GET /api/documents/{document}/sections/{section}` | path |
| `calculate_payout` | `POST /api/payouts/calculate` | body as today |
| `record_decision` | `POST /api/claims/{claim_id}/decision` | decision, payout, calculation, rationale, citations, model |
| `pay_claim` | `POST /api/claims/{claim_id}/pay` | `{amount}` |

- Key-only, in `agent_routes.py`, each with an `operation_id` and one-line docstring: `GET /api/agent/claims/{id}/plan`, `GET /api/agent/claims/{id}/state` (status, thread_id, attempts, run_id, copy_of, decision, payment, needs_sign_off, all in one read), `POST .../{id}/steps`, `.../hold`, `.../take`, `.../fail`, `.../carry-on`; `GET /api/agent/documents/{document}`, `GET /api/agent/knowledge`; `POST /api/agent/model-calls`, `/api/agent/work-times`; `GET /api/agent/configuration` and `/api/agent/runs/{id}/configuration`.
- `app.claims.take_claim(engine, claim_id, run_id) -> dict`, `fail_claim(engine, claim_id, attempt, max_attempts, message) -> str`, `set_claim_run(engine, claim_id, run_id) -> None`: the statements now in `worker.py`, moved as they are.
- `agent_env(...)` wraps a `TestClient(create_app(settings, jwks=MINT), base_url=".../api")` in `connection.use`, and sets `env.engine`.

- [x] **Step 1: Write the failing tests:** `test_the_client_sends_the_key_read_as_each_request_goes` (set the variable after importing the module), `test_a_404_is_a_lookup_error`, `test_the_tools_reach_the_records_only_through_the_client` (no `sqlalchemy` or `app.claims` import in `tools.py`, `graph.py`, `worker.py`), `test_a_decision_goes_through_the_backend_and_a_repeat_counts_as_done`.
- [x] **Step 2: Run.** `uv run pytest tests/test_agent_api.py -q`. Expected: FAIL, no module `agent.connection`.
- [x] **Step 3: Implement** the routes, then swap each direct call in `tools.py`, `graph.py` and `worker.py` for `agent/api.py`. `build_claim_graph` drops its `engine` argument. `prompts.py` keeps `Reference` for the section index.
- [x] **Step 4: Run.** `uv run pytest tests/test_graph.py tests/test_tools.py tests/test_worker.py tests/test_agent_api.py -q`. Expected: all pass. Thread-safety of a shared `TestClient` under `CountingStub` is checked here; a problem gets a client per thread in `connection.use`.

### Task 4: The dispatcher, the improver and the service over HTTP

**Files:** Create `backend/agent/improver_api.py`. Delete `backend/agent/improver_backend.py`. Modify `backend/app/agent_routes.py`, `backend/agent/dispatcher.py`, `improver.py`, `service.py`, `worker.py`, `__main__.py`, `backend/tests/*`. Test: `backend/tests/test_agent_api.py`, `test_improver.py`, `test_worker.py`.

**Interfaces:**

- Key-only runs: `POST /api/agent/dispatches`, `/api/agent/evaluations`; `GET /api/agent/runs/running`, `/api/agent/runs/{id}`; `POST .../{id}/claims`, `.../{id}/claims/{claim}/started`, `.../finished`, `.../{id}/finish`; `POST /api/agent/runs/stop-running`; `GET /api/agent/claims-to-dispatch`, `/api/agent/runs/{id}/claims`.
- Key-only rounds: one route for each function `improver_backend.py` exports today, under `/api/agent/rounds` and `/api/agent/versions`, with the same names as `operation_id`s prefixed `round_`. `decide_proposal` and `start_round` take `handler_id` in the body and check that it names a handler.
- `improver_api.py` keeps those function names, without `engine` and `ref`, so `improver.py` changes only in its calls. `RoundOpen`, `RoundStopped` and `AlreadyDecided` are raised from 409s by a `code` field in the detail.
- `RETRYABLE` gains `httpx.TransportError`.

- [x] **Step 1: Write the failing tests:** `test_only_the_improvers_client_reaches_the_scores` (import walk: claim-agent modules never import `agent.improver_api`, and no `agent/` module imports anything from `app` but `app.config`, `app.documents`, `app.reference`, `app.db`'s checkpoint constants and `app.auth.Handler`), `test_a_backend_restart_uses_up_no_attempt_and_the_claim_carries_on`, `test_a_review_is_recorded_for_the_handler_with_the_key`.
- [x] **Step 2: Run them.** Expected: FAIL.
- [x] **Step 3: Implement.** The service's `lifespan` waits for `/api/health` (decision 5) and no longer seeds. `__main__.py` loads `.env`, then works the claim over HTTP. `open_env` opens only the checkpoints.
- [x] **Step 4: Run.** `uv run pytest -q`. Expected: all pass.

### Task 5: End-to-end tests, docs and the phase's commit

**Files:** Modify `e2e/playwright.config.ts`, `e2e/stand_in_agent.py`, `e2e/tests/fixtures.ts`, `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/CODE_STANDARDS.md`.

- [x] **Step 1:** Give the test backend and service the same fixed key, the service `UNDERCOVER_BACKEND`, and the backend the judge's stand-in. Start the backend first.
- [x] **Step 2:** Run `npm run check`. Expected: "All checks passed."
- [x] **Step 3:** Add the key if its name is absent, printing nothing, from the repo root: `python -c "import secrets,re,pathlib as p;f=p.Path('.env');t=f.read_text(encoding='utf-8');None if re.search(r'^UNDERCOVER_API_KEY=',t,re.M) else f.open('a',encoding='utf-8').write('\nUNDERCOVER_API_KEY='+secrets.token_urlsafe(32)+'\n')"`. If the deny rule blocks it, ask the owner to run it with `!`.
- [x] **Step 4:** Update SPEC.md and the "agent calls `app` in Python" lines in CODE_STANDARDS.md and TECH_STACK.md.
- [x] **Step 5: Commit.** `git commit -m "feat(security): the agent reaches the backend over HTTP with an API key"`

---

## Phase 2: A check for instructions hidden in a claim

### Task 6: The check, CLM-1901 and the claim's page

**Files:** Create `backend/agent/screening.py`. Modify `backend/app/config.py`, `backend/agent/graph.py`, `worker.py`, `scripted.py`, `backend/app/reference.py`, `seed.py`, `frontend/src/pages/ClaimDetail.tsx`, `backend/tests/fakes.py`, tests that count 25 queue claims, `docs/SPEC.md`, `data/README.md`. Test: `backend/tests/test_screening.py`, `frontend/src/pages/ClaimDetail.test.tsx`.

**Interfaces:**

- `class Screening(BaseModel)`: `reason: str`, then `instructions_found: bool`, with the lab's descriptions.
- `SCREEN_INSTRUCTIONS`: the lab's text, with the name changed.
- `Settings.screening_model -> tuple[str, str]`: `("openai/gpt-6-luna", "none")`, or `(MODEL_NAME, "default")`. `config.py` stays the one place that reads `MODEL_NAME`.
- `screen_claim(model, model_id, claim: dict, record: Callable[[dict], None], attempts: int = 3) -> Screening`: `model.with_structured_output(Screening, method="json_schema", include_raw=True)`; the user message is the same `<claim_data>` text `get_claim` gives the agent; `record` runs for every call with its usage; after three unreadable answers it raises `ScreeningFailed`.
- `AgentEnv.screen_model`: a test's stub, `ScriptedScreenModel` when scripted (flags text with `PROCESSING NOTE`), or `chat_model(settings, *screening_model, max_retries=0)`. `agent_env(..., screen_model=None)` defaults to a stub that passes everything.
- Graph: `START -> screen -> (prepare | END)`. A flagged claim: trace kind `screening`, `record_decision` with `refer`, payout 0, `rationale=reason`, `citations=["Security check"]`, no rationale warning.

- [x] **Step 1: Write the failing tests:**

```python
def test_a_flagged_claim_is_referred_with_the_reason_and_the_claim_model_is_never_called(...): ...  # model.calls == 0; refer; 0; reason; ["Security check"]
def test_a_claim_that_passes_is_worked_as_before(...): ...
def test_the_check_reads_every_field_the_customer_wrote(...): ...                                   # story, summary, item descriptions, document content
def test_the_check_runs_once_and_not_again_after_a_resume(...): ...
def test_a_claim_started_again_from_its_first_message_is_checked_again(...): ...
def test_an_unreadable_answer_is_tried_three_times_and_each_call_is_costed(...): ...                # 3 model_calls rows; claim fails as any error does; model.calls == 0
def test_a_check_that_errors_fails_the_claim_without_the_claim_model(...): ...
def test_the_checks_result_is_the_first_step_of_the_work(...): ...
def test_the_check_is_not_in_the_versioned_configuration(...): ...
def test_the_check_uses_luna_at_none_and_model_name_with_no_effort(monkeypatch): ...
def test_clm_1901_is_in_the_queue_and_in_neither_evaluation_set(...): ...                           # its policy and customer resolve; no answer_key row
```

- [x] **Step 2: Run.** `uv run pytest tests/test_screening.py -q`. Expected: FAIL.
- [x] **Step 3: Implement.** The claim's page shows a `screening` step in the work list and the rationale as for any referral.
- [x] **Step 4: Run.** `npm run check`. Expected: "All checks passed."
- [x] **Step 5: Commit.** `git commit -m "feat(security): a second model checks each claim for hidden instructions"`

---

## Phase 3: The agent's own API

### Task 7: The document, the limit and the sign-off that pays

**Files:** Modify `backend/app/api.py`, `payments.py`, `backend/agent/graph.py`, `backend/agent/worker.py`, `service.py`, `backend/app/agent_client.py`, `e2e/stand_in_agent.py`, `e2e/tests/02-approval.spec.ts`, `frontend/src/api.ts` (remove `signed_off`), `backend/tests/test_round_trips.py`, `docs/SPEC.md`. Test: `backend/tests/test_agent_openapi.py`, `test_payments.py`, `test_graph.py`.

**Interfaces:**

- `GATEWAY_OPERATIONS: frozenset[str]` in `api.py`. `GET /api/agent/openapi.json` (`get_agent_openapi`) uses `fastapi.openapi.utils.get_openapi` on the routes with those ids, then has no `servers`.
- `payments.pay_claim(engine, ref, claim_id, amount) -> dict`: first, `amount > approval_limit(engine, ref)` raises `OverLimit` (403, the detail names the limit); then an amount other than the decision's payout is a 409; then as `pay` is today.
- `payments.sign_off(...)` returns a `paid` `Payment`; a repeat changes nothing.
- `pay_node`: after a wait, a payment with `approved_by` ends the node with no call. An `OverLimit` from a payment with no hold calls `hold`, then waits.

- [x] **Step 1: Write the failing tests:**

```python
def test_the_document_has_exactly_the_eight_operations_and_no_servers(client): ...
def test_the_document_has_no_key_input_and_keeps_amount_and_docstrings(client): ...
def test_the_document_needs_the_key_or_a_sign_in(client): ...
def test_a_payment_over_the_limit_on_a_claim_never_approved_is_a_403_naming_the_limit(client): ...  # also an unknown claim id
def test_the_limit_comes_from_the_setting(client): ...
def test_a_sign_off_pays_the_claim_once(client): ...  # two sign-offs; one paid step; claim decided; no call to the service
def test_a_sign_off_does_not_need_the_agents_service(backend_with_service_down): ...
def test_an_agent_resumed_after_a_sign_off_makes_no_payment_call(spy): ...
def test_a_limit_lowered_after_the_decision_holds_the_payout_for_a_handler(...): ...
```

- [x] **Step 2: Run.** `uv run pytest tests/test_agent_openapi.py -q`. Expected: FAIL.
- [x] **Step 3: Implement.** Delete what decision 3 lists. `02-approval.spec.ts` expects the claim paid after the sign-off.
- [x] **Step 4: Run.** `npm run check`. Expected: "All checks passed."
- [x] **Step 5: Commit.** `git commit -m "feat(security): the agent's own API document, a limit before the claim, and a sign-off that pays"`

---

## Phase 4: Ready for Lambda

### Task 8: The dashboard's build, served by the backend

**Files:** Create `scripts/build.mjs`. Modify `package.json`, `.gitignore`, `frontend/vite.config.ts`, `backend/app/api.py`, `backend/app/config.py`. Test: `backend/tests/test_dashboard_files.py`.

**Interfaces:** `npm run build` loads `.env` if it exists, requires `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` from the environment, and runs `vite build --outDir ../dashboard/dist --emptyOutDir`. `Settings.dashboard_dir: str` defaults to `ROOT/dashboard/dist`. When the folder exists at `create_app`, a `GET /{path:path}` route with `include_in_schema=False` is added after the router.

- [x] **Step 1: Write the failing tests:** `test_root_and_a_page_get_index_html`, `test_a_file_is_served_at_its_own_path`, `test_an_unknown_api_path_is_a_json_404`, `test_with_no_build_the_backend_runs_as_today`, and, parametrized on `/..%2F..%2F.env` and `/assets/../../data/claims.json`, `test_a_path_outside_the_build_gets_index_html_never_the_file`.
- [x] **Step 2: Run.** Expected: FAIL.
- [x] **Step 3: Implement.** Resolve the path and require `is_relative_to(dist)` and `is_file()`. If the build prints a chunk-size warning, split the Supabase client with `manualChunks`.
- [x] **Step 4: Run** the tests and `npm run build`. Expected: pass, and no line with "warning" in the output.

### Task 9: Lambda: the handler, connections and the server group

**Files:** Create `backend/__init__.py`, `backend/main.py`. Modify `backend/pyproject.toml`, `backend/uv.lock`, `backend/app/db.py`, `config.py`, `configs.py`, `scripts/check.mjs`, `docs/TECH_STACK.md`, `docs/PROCESS.md`, `data/README.md`. Test: `backend/tests/test_lambda.py`.

**Interfaces:**

- `backend/main.py`: `app`, then at the end `handler = Mangum(app, lifespan="off")`.
- `Settings.on_lambda: bool`, from `AWS_LAMBDA_FUNCTION_NAME`.
- On Lambda, `_postgres_engine` uses `NullPool`, `connect_args={"prepare_threshold": None}`, no `SET search_path`, and `execution_options(schema_translate_map={None: SCHEMA})`.
- `server` group: `fastapi`, `uvicorn`, `mangum`, `pydantic-settings`, `sqlalchemy`, `psycopg[binary]`, `httpx`, `pyjwt[crypto]`, `openrouter`, plus anything the check below finds.
- `load_instructions` and `load_recipe` move to `app/configs.py`, reading `backend/agent/*.md`, which are in the package. `agent/prompts.py` no longer has them.

- [x] **Step 1: Write the failing tests:**

```python
def test_the_handler_answers_a_function_url_event_for_health(): ...
def test_the_handler_serves_index_html_for_root_and_a_page(dist): ...
def test_on_lambda_a_connection_is_opened_per_request_with_prepared_statements_off(monkeypatch): ...  # NullPool; prepare_threshold None
def test_on_lambda_every_statement_names_the_schema(monkeypatch): ...                                  # compiled SQL has undercover.claims; no SET
def test_the_app_imports_nothing_from_the_agent(): ...                                                 # subprocess: no agent, langchain or langgraph in sys.modules
def test_the_app_starts_with_only_the_three_variables(monkeypatch): ...
def test_the_app_writes_no_files(tmp_path, monkeypatch): ...                                           # read-only stand-in for open(..., "w") during requests
```

- [x] **Step 2: Run.** `uv run pytest tests/test_lambda.py -q`. Expected: FAIL.
- [x] **Step 3: Implement.** Add a step to `check.mjs`, from the repo root: `uv run --project backend --isolated --only-group server python -c "import backend.main"`. Add the Deployment section to TECH_STACK.md: `npm run build`, `dashboard/dist`, what's in the package, `backend.main.handler`, `backend.main:app`, the three variables.
- [x] **Step 4: Run.** Clean worktree: `git worktree add ..\secure-check HEAD`, install, `npm run check`, remove the worktree. Expected: "All checks passed", with no `.env` there, and `git status` in the project shows `.venv` untouched.
- [x] **Step 5: Commit.** `git commit -m "feat(deploy): a Lambda handler, the dashboard served by the backend and a server dependency group"`

---

## Phase 5: Live checks

Do these last, after one `npm run seed`, on the default model. Record every result and what it spent.

### Task 10: Steps 1 to 3

- [x] **Step 1:** `npm run seed`, then `npm run dev`. Check the OpenRouter key's credit first.
- [x] **Step 2:** Signed in as Dana, the dashboard works. `curl -i -X POST http://127.0.0.1:8000/api/claims/CLM-1001/pay -H "content-type: application/json" -d "{\"amount\":1}"`. Expected: 401.
- [x] **Step 3:** `cd backend; uv run -m agent CLM-1001`. Expected: one line with its outcome.
- [x] **Step 4:** A dispatch works the rest of the queue. Expected: CLM-1901 referred with the check's reason, shown on the claim; the others decided as before.
- [x] **Step 5:** An evaluation of the evaluation set's tiny five. Expected: none flagged.
- [x] **Step 6:** Dana signs off a held payout. Expected: paid once.

### Task 11: The built app on its own

- [x] **Step 1:** `npm run build`. Stop `npm run dev`. From the repo root, start `uvicorn backend.main:app --port 8000` with `.env` in its environment.
- [x] **Step 2:** Through the Playwright MCP server, Dana signs in at `http://127.0.0.1:8000` and sees the claims. The password goes through the established script route if the MCP would have to receive it in a tool call; ledger that as a ruling.

### Task 12: Lambda's conditions and the package

- [x] **Step 1:** Start the backend with `AWS_LAMBDA_FUNCTION_NAME=local` and `DATABASE_URL` on port 6543. Expected: a signed-in handler gets the claims, with several requests in a row.
- [x] **Step 2:** In a temp folder, following `labs/week3day2_lab2.ipynb`'s packaging cells with `--python-platform x86_64-manylinux2014 --python-version 3.12`, pack `backend/` (without tests and `__pycache__`), `data/` and `dashboard/dist/`. Expected: under 50 MB zipped. Report the size.
- [x] **Step 3:** Record every result and the spending in `docs/STATUS.md`. Move this plan to `docs/plans/` when review is done.
- [x] **Step 4: Commit.** `git commit -m "docs: record the live checks of the security changes"`
