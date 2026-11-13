# The UnderCover Copilot: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an agent for handlers in a sidebar on every signed-in page. It looks things up, starts the agent's work, sends decisions back and signs them off, all as the handler who asked and with their sign-in. It shows each step as it takes it, asks before it pays, keeps every conversation and remembers each handler.

**Architecture:**
- **The graph.** The Copilot is a hand-written LangGraph `StateGraph` in `backend/agent/copilot/`: a `recall` node, a `model` node, LangGraph's `ToolNode`, a `confirm` node that pauses with `interrupt()`, and a `save` node. It is checkpointed per conversation with the claim agent's `Checkpoints`.
- **The handler's sign-in.** The sign-in travels in the graph's context (`context_schema`, read through `ToolRuntime`), never in the config or the state.
- **The app.** The Copilot is a `BedrockAgentCoreApp` of its own (`agent/copilot/entrypoint.py`, started on Runtime by `runtime/copilot.py`), served on 127.0.0.1:8081 on the laptop. The app keeps conversations, turns and steps in three new tables. It starts each turn and each answer to a confirmation with the conversation's id as the Runtime session id. The Copilot reports every step back through the app's routes with the handler's sign-in.
- **The sidebar.** The dashboard polls one conversation read once a second while a turn works.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy Core, LangGraph 1.2 (`StateGraph`, `ToolNode`, `interrupt`, `Command`, `context_schema`), LangChain 1.4 (`ToolRuntime`), `langchain-mcp-adapters` (`MultiServerMCPClient`, new), `langgraph-checkpoint-aws` (`AgentCoreMemoryStore`), `bedrock-agentcore` 1.26 (`BedrockAgentCoreApp`), boto3. React 19 with Vitest; `react-markdown` and `remark-gfm` (new); Playwright.

**Spec:** `docs/COPILOT.md`

SIDENOTE: For consistency with the course, please build the Copilot the way the course has built its agents.
- **The agent loop** is a LangGraph `StateGraph` written by hand: a node that calls the model, LangGraph's `ToolNode`, and the loop between them, with a `PostgresSaver` checkpointer, as in `labs/week2day3.ipynb` and the claim agent's graph. Please don't use LangChain's `create_agent` for it.
- **The pause before a sign-off** is an `interrupt()` in a node of its own, resumed with `Command(resume=...)`. LangGraph runs an interrupted node again from the top when it resumes, which is why the claim agent's wait for a handler is also a node of its own.
- **The handler's sign-in** reaches the graph and its tools in the graph's context (`context_schema`, read through `ToolRuntime`). It never goes in the config, whose values LangGraph saves with each checkpoint.
- **The Copilot is an AgentCore app of its own,** made with `BedrockAgentCoreApp`, with one entrypoint and each turn's work registered as an async task, as the claim agent's AgentCore app does. It is synchronous throughout, as the claim agent is. `labs/week3day4_lab1.ipynb` and `labs/undercoverlab/` show a pause in one Runtime session resumed in another.
- **Its memory** is `AgentCoreMemoryStore` as a LangGraph store, as the claim agent's memory is, with the Memory itself made as in `labs/week3day3.ipynb`.
- **The context layer:** it reaches it over MCP with `MultiServerMCPClient`, as in `labs/week2day2.ipynb`, and through the Gateway as in `labs/week3day2_lab2.ipynb` and `labs/week3day3.ipynb`.
- **Deployment:** `labs/week3day4_lab2.ipynb` deploys the claim agent and points the app at it, and `labs/week3day5_lab1.ipynb` deploys the Copilot beside it, so please match the names that notebook uses.
- **Read these notebooks without running them.** Where a notebook and this document differ, follow this document. Include this sidenote in your plan too.

(In this repo the course's notebooks are on `../undercover`'s branches: `git show origin/week3day5-start:labs/week3day5_lab1.ipynb`, and the same for `labs/undercoverlab/`. The names that notebook uses, which this plan keeps:
- the runtime `copilot`, deployed as `undercover_default_copilot`;
- the Memory `undercover_copilot_memory`, with the strategies `handler_preferences` on `/handlers/{actorId}/preferences/` and `handler_facts` on `/handlers/{actorId}/facts/`;
- the settings `UNDERCOVER_COPILOT_MEMORY_ID` and `UNDERCOVER_COPILOT_RUNTIME`.)

## Global Constraints

- **Our layout:** the Python root is `backend/` (`app/`, `agent/`, `context/`, `tests/`, `pyproject.toml`, `uv.lock`), and the dashboard is `frontend/`. The spec's `docs/BRAND.md` is our `docs/DESIGN.md` with `frontend/src/styles/tokens.css`, including its dark tokens.
- **The Copilot's code** lives in `backend/agent/copilot/` and imports nothing from `app`. Runtime starts it from `runtime/copilot.py`.
- **The model:** `openai/gpt-6-luna` with reasoning effort `low`. When `MODEL_NAME` is set, that model runs with effort `default`. A turn makes at most **25** model calls.
- **The laptop:** the Copilot listens on `127.0.0.1:8081` and asks for no key. The app reaches it at `UNDERCOVER_COPILOT`, default `http://127.0.0.1:8081`. When `UNDERCOVER_COPILOT_RUNTIME` holds an ARN, the app sends every turn to that runtime.
- **Conversation ids** are `conversation-<uuid4>` (49 characters, all letters, digits and hyphens). The id is also the Runtime session id and the Memory's session.
- **The sign-in:** every call the Copilot makes to the app carries `Authorization: Bearer <handler's token>` and never `X-API-Key`. The token is never saved: not in the checkpoint state, the steps, the memory or any log line.
- **Credentials on Runtime:** the Copilot fetches only `OPENROUTER_API_KEY` and `DATABASE_URL` from AgentCore Identity.
- **Timing:**
  - The Copilot waits for a dispatch or an evaluation by polling every **3 s**, for at most **15 min**.
  - A turn still working **20 min** after it started, or after its confirmation was answered, shows as `stopped`.
  - The sidebar polls every **1 s** while a turn works, and not at all while the conversation is idle.
  - The dashboard renews the sign-in when less than **20 min** is left on it.
- **Success criteria:** straight through ≥ **0.70** on the whole held-out set; accuracy ≥ the `objectives.accuracy` setting; and every queue claim paid over the approval limit carries a handler's sign-off.
- **The memory** is on only when `UNDERCOVER_COPILOT_MEMORY_ID` is set. It searches `("handlers", <handler_id>)` and saves events with the handler as the actor and the conversation as the session.
- **Retries:** a Runtime `ConflictException` is retried up to **4** times, **1 s** apart. Nothing else is retried.
- **Tests and checks:** tests never call a model, AWS, Supabase Auth or the Gateway, and `npm run check` never reads `.env`. `UNDERCOVER_COPILOT_RUNTIME` and `UNDERCOVER_COPILOT_MEMORY_ID` are blanked in `conftest.py`, in `scripts/check.mjs` and in the e2e backend's environment.
- **Windows:** everything `npm run dev` starts works on Windows.
- **Commits and live runs:** commit at the end of each phase. The live checks come last, after one `npm run seed`.

## Rulings made while planning

- **The working tree before Phase 1 (needs the owner's word).** The uncommitted deploy work fixes the gap PLAN_RUNTIME left open:
  - `cdk-stack.ts` stages `backend/agent`;
  - `scripts/prepare_runtime.py` writes `runtime/pyproject.toml`;
  - the entrypoint accepts the CLI 1.0.2 credential names.

  But it also leaves `agentcore/agentcore.json` with no runtimes and no credentials, which looks like a CLI side effect. Task 0 commits that work and puts back the `agent` runtime and the three credentials from HEAD, before the Copilot's runtime is added beside them.
- **The context layer's tools come from `MultiServerMCPClient`.** The spec asks for it, and the claim agent's bridge does not use it.
  - `agent/copilot/context_tools.py` loads them with `MultiServerMCPClient` on the same route the claim agent picks (`UNDERCOVER_GATEWAY_URL` with SigV4, or `UNDERCOVER_CONTEXT`).
  - On the Gateway it keeps only the `undercover-context___*` tools.
  - Each tool is wrapped to run synchronously on a fresh event loop, as `mcp_bridge` does, and never with `asyncio.run`.
  - Each tool is renamed to its own name.
- **The card is built from the records, by the app.** The Copilot's pause report sends only `claim_id`. The app builds the confirmation card (claim, customer, amount, the agent's reason) from its own tables and stores it with the turn. The `interrupt()` payload is that card as the app returned it.
- **The e2e stand-in Copilot is the real Copilot app on a scripted model.** It runs `ScriptedCopilotModel` (`agent/copilot/scripted.py`) with the provider `scripted`, so the stand-in plays a fixed turn through the app with the handler's sign-in. That way the e2e tests also cover the real graph, cards and pause.
- **One turn at a time** is kept by `copilot_conversations.open_turn_id`, changed with a conditional UPDATE, as `work_lock` is. While a confirmation waits, a new message is refused: the dashboard asks the handler to answer the card first.
- **An expired sign-in partway through a turn.** The Copilot's first call is `GET /api/me`. If it is refused, the turn ends with no model call, and since it cannot report with that token, the turn shows as `stopped` after 20 minutes. The dashboard always sends a token with at least 20 minutes left, so a turn reaches this only by working longer than that.
- **The Copilot's Runtime session.** Its `idleRuntimeSessionTimeout` is 300 s, so a turn soon after the last one reuses the session. The `agent` runtime keeps its 60 s.
- **The evaluation the Copilot reads** is the report without its `claims` list. That list carries the handlers' decisions on the labelled sets, which the Copilot must never see.

## Review Focus

1. **A confirmation answered twice, or after a reload on another day.** The second answer gets 409. The resumed graph pays once, and nothing before the `interrupt()` runs again (Task 7 tests this).
2. **Two messages sent at once in one conversation.** Exactly one turn starts and the other gets 409 (Task 2 tests this).
3. **A handler opening, answering or posting to another handler's conversation.** Each gets 404, and the Copilot's report routes refuse a turn that is not the signed-in handler's (Task 2 tests this).
4. **A request to the app carrying both the key and a sign-in.** The Copilot's routes refuse it, and the Copilot's own client never sends the key (Tasks 2 and 5 test this).
5. **A wait that outlives its 15 minutes, or a run that ends `failed`/`stopped`.** The answer says so and gives no figures for the first two criteria (Task 8 tests this).

---

## Task 0: Settle the working tree (before Phase 1)

- [ ] Run `git diff HEAD -- agentcore backend scripts .gitignore` and show the owner what is uncommitted.
- [ ] Put back `runtimes[agent]` and the three `credentials` in `agentcore/agentcore.json` from HEAD, and keep the rest of the working-tree changes.
- [ ] Run `cd backend; uv run pytest tests/test_entrypoint.py -q`. Expected: PASS.
- [ ] Commit with `chore: the deploy staging from backend/, and runtime/pyproject.toml prepared before a deploy`.

---

## Phase 1: The app's side of the Copilot (spec items 2, 3, 8)

### Task 1: The reads a handler may use, and the count of payouts over the limit

**Files:**
- Modify: `backend/app/api.py` (the routes below, on `router`, so they need a signed-in handler)
- Test: `backend/tests/test_copilot_reads.py`

**Interfaces (Produces):**
- **`GET /api/customers?name=<text>`** (`find_customers`) answers `[{customer_id, name, customer_since}]`. The match is a case-insensitive substring of the name, or an exact `customer_id`. An empty `name` answers 422.
- **`GET /api/customers/{customer_id}`** (`get_customer`) answers `{customer, policies: [...], previous_claims: [...], queue_claims: [{claim_id, status, decision, payout}]}`. The policies are those whose `customer_id` matches. The queue claims leave out evaluation copies. An unknown customer answers 404.
- **`GET /api/policies/{policy_id}`** (`get_policy_by_id`) answers the policy, or 404.
- **`GET /api/dispatches/{run_id}`** (`get_dispatch`) answers the same `run_report` as `/dispatches/latest`. An unknown run, or a run that is not a dispatch, answers 404.
- **`GET /api/payouts/over-limit`** (`count_payouts_over_limit`) answers `{approval_limit, paid_over_limit, signed_off}`:
  - `paid_over_limit` counts payments with `status='paid'` and `amount > approval_limit` on queue claims (`copy_of IS NULL`);
  - `signed_off` counts those whose `approved_by` is a handler id from `handlers.json`.

- [ ] **Step 1: Write the failing tests.**
  - `test_find_customers_by_part_of_a_name_or_an_id`.
  - `test_a_customer_with_policies_earlier_claims_and_queue_claims`.
  - `test_a_policy_by_id`.
  - `test_a_dispatch_by_id_and_404_for_an_evaluation`.
  - `test_over_limit_counts_with_none_paid` (expects `{…, "paid_over_limit": 0, "signed_off": 0}`).
  - `test_over_limit_counts_a_signed_off_payout_and_leaves_out_copies`.
  - `test_each_read_needs_a_sign_in` (no token gives 401, and `X-API-Key` alone gives 401).
- [ ] **Step 2: Run them.** Run `cd backend; uv run pytest tests/test_copilot_reads.py -q`. Expected: FAIL (404s).
- [ ] **Step 3: Implement the five routes** in `api.py`, with docstrings that follow TECH_STACK's API rules (operation ids as above).
- [ ] **Step 4: Run them again.** Same command. Expected: PASS.

### Task 2: Conversations, turns and steps, kept by the app

**Files:**
- Create: `backend/app/copilot.py` (the records), `backend/app/copilot_routes.py` (the router)
- Modify: `backend/app/db.py` (three tables, plus `drop_tables`), `backend/app/api.py` (include the router; pass the request's bearer token on)
- Test: `backend/tests/test_copilot_routes.py`

**Interfaces (Produces):**
- **The tables:**
  - `copilot_conversations(conversation_id String(100) PK, handler_id, title String(120), open_turn_id Integer NULL, created_at, updated_at)`.
  - `copilot_turns(turn_id PK, conversation_id, message Text, status, answer Text NULL, error Text NULL, confirmation Text NULL /*JSON card*/, confirmation_answer NULL, model_calls Integer default 0, cost_usd Float NULL, started_at, resumed_at NULL, ended_at NULL)`. The status is one of `working|waiting|done|failed|stopped`.
  - `copilot_steps(step_id PK, turn_id, conversation_id, kind, tool, main_input, status, input Text, output Text NULL, sources Text NULL, reason NULL, started_at, ended_at NULL)`. The kind is one of `claims|customers|context|work|memory|action`, and the status one of `working|done|refused|failed`.
- **The records** in `app/copilot.py`:
  - `new_conversation_id() -> str`.
  - `create_conversation(engine, handler_id) -> str`.
  - `list_conversations(engine, handler_id) -> list[dict]`, newest first.
  - `conversation_view(engine, conversation_id, handler_id, now) -> dict`. It raises `LookupError` for an unknown conversation or another handler's, and shows a `working` turn as `stopped` once `now - (resumed_at or started_at) > STALE = 20 min`.
  - `start_turn(engine, conversation_id, handler_id, message, now) -> int`. It raises `TurnOpen(reason)`. It also sets the title from the first message (at most 120 characters).
  - `answer_confirmation(engine, conversation_id, handler_id, answer, now) -> int`. The answer is `"sign_off"` or `"cancel"`. It raises `NotWaiting`.
  - `turn_of(engine, turn_id, handler_id) -> Mapping`, which raises `LookupError` when the turn is not this handler's.
  - `record_step(...) -> int` and `finish_step(...)`.
  - `pause_turn(engine, ref, turn_id, claim_id) -> dict`, which returns the card `{claim_id, payment_id, customer_name, amount, reason}`, read from `payments`, `claims` and `decisions`.
  - `end_turn(..., answer, model_calls, cost_usd)` and `fail_turn(..., reason)`, which both clear `open_turn_id`.
  - `undo_turn(engine, turn_id, reason)`, which fails the turn when the Copilot never took it.
- **The routes** (a router with `signed_in`; any request carrying `X-API-Key` gets 403 `"the Copilot's routes take a handler's sign-in, never the agent's key"`):
  - The dashboard's routes:
    - `POST /api/copilot/conversations` answers 201 with `{conversation_id}`.
    - `GET /api/copilot/conversations`.
    - `GET /api/copilot/conversations/{id}` answers `{conversation_id, title, turns: [{turn_id, message, status, answer, error, model_calls, cost_usd, seconds, confirmation, steps: [...]}]}`.
    - `POST /api/copilot/conversations/{id}/messages {text: str (1..4000)}` answers 202 with `{turn_id}`. A turn already working gives 409 `"the Copilot is still working on your last message"`, and a waiting one gives 409 `"answer the confirmation first"`.
    - `POST /api/copilot/conversations/{id}/confirmation {answer: "sign_off"|"cancel"}` answers 202.
  - The Copilot's report routes, owner-checked through `turn_of`:
    - `POST /api/copilot/turns/{turn_id}/steps` answers `{step_id}`.
    - `POST /api/copilot/steps/{step_id}`.
    - `POST /api/copilot/turns/{turn_id}/pause {claim_id}` answers the card.
    - `POST /api/copilot/turns/{turn_id}/end`.
    - `POST /api/copilot/turns/{turn_id}/fail`.
  - Another handler's conversation or turn is always a 404.
- **The payloads to the Copilot:**
  - `{"operation": "turn", "conversation_id", "turn_id", "message", "token"}`;
  - `{"operation": "answer", "conversation_id", "turn_id", "answer", "token"}`;
  - `{"operation": "health"}`.

- [ ] **Step 1: Write the failing tests.** They use a recorder in place of the Copilot client, and `MINT` tokens for HND-01 (Dana) and HND-02 (Marcus).
  - `test_a_message_starts_a_turn_and_sends_the_token_and_the_session`. The payload carries `token` equal to the request's token, and the session is the conversation id.
  - `test_a_second_message_while_working_is_refused`.
  - `test_two_messages_at_once_start_one_turn`, with two threads and a pool of 2.
  - `test_a_waiting_turn_refuses_a_message_and_is_never_stopped`.
  - `test_a_turn_working_20_minutes_shows_stopped_and_a_new_message_goes_through`, which moves `now` on.
  - `test_a_confirmation_answered_twice_gets_409`.
  - `test_another_handler_gets_404_on_read_message_confirm_and_reports`.
  - `test_a_request_with_the_agent_key_is_refused`.
  - `test_the_card_is_read_from_the_records`. The pause body carries only the claim, and the card's amount and reason match the `payments` and `decisions` tables.
  - `test_a_copilot_that_was_not_reached_fails_the_turn_and_frees_the_conversation`.
  - `test_reseed_drops_the_conversations`.
  - `test_the_token_is_never_stored`, which scans every column of the three tables for the token.
- [ ] **Step 2: Run them.** Run `cd backend; uv run pytest tests/test_copilot_routes.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement the tables, `app/copilot.py` and `app/copilot_routes.py`.** `start_turn` claims the conversation with `UPDATE copilot_conversations SET open_turn_id=:new WHERE conversation_id=:id AND (open_turn_id IS NULL OR open_turn_id=:stale)`, where `:stale` is the open turn only when it shows `stopped`. A stale turn is marked `stopped` in the same transaction.
- [ ] **Step 4: Run them again.** Same command. Expected: PASS.

### Task 3: The app's client for the Copilot

**Files:**
- Create: `backend/app/copilot_client.py`
- Modify: `backend/app/config.py` (`copilot_url` from `UNDERCOVER_COPILOT`, `copilot_runtime_arn` from `UNDERCOVER_COPILOT_RUNTIME`, both from the process environment only; plus the `copilot_model` property and `COPILOT_MODEL = ("openai/gpt-6-luna", "low")`), `backend/tests/conftest.py`, `scripts/check.mjs`, `e2e/playwright.config.ts` (blank both new settings)
- Test: `backend/tests/test_copilot_client.py`

**Interfaces (Produces):**
- `send(settings, payload: dict, session_id: str, http=httpx, client=None) -> dict`. It raises `agent_client.AgentRefused`, `AgentNotReached` or `AgentMaybe`, the same classes the agent client raises.
  - Over HTTP it posts to `{copilot_url}/invocations` with no key and a 10 s timeout.
  - Over Runtime it calls `invoke_agent_runtime(agentRuntimeArn=copilot_runtime_arn, runtimeSessionId=session_id, …)`, retrying `ConflictException` up to 4 times, 1 s apart (`sleep` can be injected).
- `Settings.copilot_model -> tuple[str, str]` returns `(MODEL_NAME, "default")` when `MODEL_NAME` is set, otherwise `COPILOT_MODEL`.

- [ ] **Step 1: Write the failing tests.**
  - `test_over_http_with_no_key`.
  - `test_over_runtime_with_the_conversation_as_session`.
  - `test_a_conflict_is_retried_then_goes_through`.
  - `test_four_conflicts_are_not_reached`.
  - `test_another_client_error_is_not_retried`.
  - `test_copilot_model_is_luna_low_or_model_name`.
- [ ] **Step 2: Run them.** Run `cd backend; uv run pytest tests/test_copilot_client.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement it**, reusing `agent_client._answer` and its exception classes. Wire the routes from Task 2 to it through `hand_over`'s pattern: a refusal or not-reached calls `undo_turn`, and `AgentMaybe` changes nothing.
- [ ] **Step 4: Run the tests again.** Run `cd backend; uv run pytest tests/test_copilot_client.py tests/test_copilot_routes.py -q`. Expected: PASS.
- [ ] **Step 5: Run the backend suite and commit.** Run `cd backend; uv run pytest -q`. Expected: all pass. Commit with `feat: the app keeps the Copilot's conversations, turns and steps, and the reads it needs`.

---

## Phase 2: The Copilot's loop, as the handler (spec items 2, 3, 4, 5)

### Task 4: The sign-in in the context, the app with the handler's token, and who is asking

**Files:**
- Create: `backend/agent/copilot/__init__.py`, `backend/agent/copilot/asker.py`, `backend/agent/copilot/app_api.py`
- Modify: `backend/agent/connection.py` (`call(..., token: str | None = None)`)
- Test: `backend/tests/test_copilot_app_api.py`

**Interfaces (Produces):**
- `connection.call(method, path, json=None, params=None, token=None)`. With a token it sends `Authorization: Bearer <token>` and **no** `X-API-Key`, even when `UNDERCOVER_API_KEY` is set. Without one, it behaves as before.
- `@dataclass(frozen=True) class Asker: token: str; handler: dict; conversation_id: str; turn_id: int`. This is the graph's `context_schema`. Its `__repr__` hides the token (`Asker(handler=HND-01, …)`).
- In `app_api.py`, every function takes `token` first:
  - `who(token) -> dict`;
  - `get(token, path, params=None) -> Any`;
  - `post(token, path, body) -> Any`;
  - `record_step(token, turn_id, kind, tool, main_input, input) -> int`;
  - `finish_step(token, step_id, status, output, sources=None, reason=None)`;
  - `pause(token, turn_id, claim_id) -> dict`;
  - `end(token, turn_id, answer, model_calls, cost_usd)`;
  - `fail(token, turn_id, reason)`.

- [ ] **Step 1: Write the failing tests.**
  - `test_a_call_with_a_token_never_sends_the_key`, with `UNDERCOVER_API_KEY` set in the environment and an httpx `MockTransport` asserting the headers.
  - `test_the_asker_repr_hides_the_token`.
  - `test_who_returns_the_handler_and_a_refusal_raises`.
- [ ] **Step 2: Run them.** Run `cd backend; uv run pytest tests/test_copilot_app_api.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run them again.** Same command. Expected: PASS.

### Task 5: The tools and their cards

**Files:**
- Create: `backend/agent/copilot/tools.py`, `backend/agent/copilot/context_tools.py`, `backend/agent/copilot/cards.py`
- Modify: `backend/pyproject.toml` (add `langchain-mcp-adapters` to the dependencies and to the `runtime` group, then lock)
- Test: `backend/tests/test_copilot_tools.py`

**Interfaces (Produces):**
- **The tools** come from `build_tools(context_tools: list[BaseTool]) -> list[BaseTool]`. Each one reads `runtime: ToolRuntime[Asker]` and calls `app_api` with `runtime.context.token`.
  - **Lookups:**
    - `list_claims()`, `get_claim(claim_id)`, `get_claim_steps(claim_id)`;
    - `list_held_payouts()`, which reads `/approvals`;
    - `get_policy(policy_id)`, `find_customers(name)`, `get_customer(customer_id)`;
    - `get_latest_dispatch()`;
    - `list_evaluations()`, and `get_evaluation(run_id)`, which drops the report's `claims`;
    - `get_settings()`, which returns `{approval_limit, objectives, agents_at_once}` only.
  - **Actions:**
    - `start_dispatch(workers: int | None)`, which uses `agents_at_once` when it is None;
    - `start_evaluation(eval_set: Literal["evaluation","held_out"], tiny: bool, workers: int | None)`;
    - `send_back(claim_id, note)`;
    - `sign_off(claim_id)`, in Task 7.
  - **The wait and the needs:** `wait_for_run` and `what_needs_me`, in Task 8.
  - **The criteria:** `check_success_criteria`, in Task 8.
  - **The context layer:** the seven tools from `load_context_tools()`.
  - **Refusals:** a refusal from the app (`Refused`, `LookupError`) comes back to the model as `"Refused: <reason>"`, and its card is `refused`. A transport error is raised, and the card is `failed`.
- **`load_context_tools(env=os.environ) -> list[BaseTool]`** builds a `MultiServerMCPClient` with one connection:
  - `streamable_http` to `UNDERCOVER_GATEWAY_URL` with `auth=SigV4Auth(boto3.Session())`;
  - or to `UNDERCOVER_CONTEXT` (default `http://127.0.0.1:8006/mcp`).

  It keeps only the seven `CONTEXT_TOOLS` names (with or without the `undercover-context___` prefix), renames each to its own name, and wraps it synchronously. A test can pass in a `client`.
- **`cards.py`:**
  - `KIND: dict[str, str]` maps each tool name to `claims|customers|context|work|memory|action`.
  - `ACTIONS = {"start_dispatch", "start_evaluation", "send_back", "sign_off"}`.
  - `main_input(tool, args) -> str`, for example `get_claim CLM-1012` gives `"CLM-1012"`.
  - `trim(value, limit=2000) -> str`.
  - `carded(request, execute)` is the `ToolNode(wrap_tool_call=...)` hook. It calls `record_step` before and `finish_step` after, with the context layer's `sources` (file and owner) from a context tool's answer.

- [ ] **Step 1: Write the failing tests.** They run with a fake app (an httpx `MockTransport` recording each request) and a fake MCP client.
  - `test_every_tool_call_carries_the_handlers_token_and_never_the_key`.
  - `test_a_lookup_records_a_working_card_then_done_with_its_seconds`.
  - `test_a_refusal_is_told_to_the_model_and_carded_refused`, for example a 409 on `start_dispatch` saying "a dispatch is already running".
  - `test_start_dispatch_uses_agents_at_once_when_not_given`.
  - `test_get_evaluation_never_returns_the_handlers_decisions`.
  - `test_context_tools_on_the_gateway_keep_only_the_context_target_and_show_own_names`.
  - `test_a_context_card_carries_its_file_and_owner`.
- [ ] **Step 2: Run them.** Run `cd backend; uv run pytest tests/test_copilot_tools.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement it.** Run `uv add langchain-mcp-adapters` and `uv add --group runtime langchain-mcp-adapters`.
- [ ] **Step 4: Run them again.** Same command. Expected: PASS.

### Task 6: The graph, its instructions and the model

**Files:**
- Create: `backend/agent/copilot/graph.py`, `backend/agent/copilot/instructions.md`, `backend/agent/copilot/scripted.py`
- Modify: `backend/agent/settings.py` (`COPILOT_MODEL`, the `copilot_model` property, as in Task 3)
- Test: `backend/tests/test_copilot_graph.py`

**Interfaces (Produces):**
- `class CopilotState(MessagesState): model_calls: int = 0; cost_usd: float = 0.0; sign_off: dict | None = None`.
- `build_copilot_graph(model, checkpointer, tools, store, instructions: str, max_model_calls: int = 25) -> CompiledStateGraph`, compiled with `context_schema=Asker` and the checkpointer.
  - Edges: `START → recall → model`. From `model`: tool calls go to `tools`, and none go to `save → END`. From `tools`: a pending `sign_off` goes to `confirm`, otherwise back to `model`. `confirm → model`.
  - `model` raises `TooManyModelCalls` past the limit. It adds OpenRouter's `usage.cost` to `cost_usd`, as `events.py` reads it.
  - The system prompt is `instructions` with the handler's name, role and whether they may sign off over the limit. It is never saved in the messages.
- **`instructions.md`** gives the goal, who the Copilot works for, what it can see and do, what it cannot do yet (settings, configuration and improvement rounds, each with where on the dashboard to do it), the three criteria, and how to answer: briefly, in plain business English and US dollars, with every figure taken from a tool. It does not script the Copilot's steps.
- **`ScriptedCopilotModel(BaseChatModel)`** is for the e2e stand-in.
  - A message containing "sign off" gives `list_held_payouts`, then `sign_off(<first held claim>)`.
  - Any other message gives `list_claims`, `start_dispatch(workers=2)`, then `get_claim("CLM-0000")` (a refusal), then a markdown answer with a list and a small table.
  - After a tool result naming a confirmation it answers in plain text.

- [ ] **Step 1: Write the failing tests.** They use a stub model that returns scripted `AIMessage`s, a SQLite saver, and a fake app.
  - `test_the_loop_calls_tools_until_the_model_answers`.
  - `test_more_than_25_model_calls_fail_the_turn`.
  - `test_the_token_is_in_no_checkpoint`, which reads every checkpoint and write of the thread and checks that `"tok-"` is in none of them.
  - `test_the_system_prompt_names_the_handler_and_is_not_saved`.
  - `test_the_scripted_model_plays_the_e2e_turn`.
- [ ] **Step 2: Run them.** Run `cd backend; uv run pytest tests/test_copilot_graph.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run them again.** Same command. Expected: PASS.

### Task 7: The sign-off pauses, and a fresh Copilot carries on

**Files:**
- Modify: `backend/agent/copilot/tools.py` (`sign_off`), `backend/agent/copilot/graph.py` (`confirm`)
- Create: `backend/agent/copilot/turns.py`
- Test: `backend/tests/test_copilot_pause.py`

**Interfaces (Produces):**
- **`sign_off(claim_id)`** checks in code, without asking the model:
  - the claim has a held payment;
  - the handler may sign it off: `handler.can_approve_over_limit` or `amount <= approval_limit`.

  If either check fails, it answers `"Refused: <reason>"` and never pauses. If both pass, it returns `Command(update={"sign_off": {"claim_id": …}, "messages": [ToolMessage("Waiting for the handler to confirm on the card.", …)]})`.
- **`confirm`**: its first statement is `answer = interrupt(state.sign_off)`. Then:
  - On `"sign_off"`, it posts `/api/payments/PAY-<claim>/approve` with the token, under a card of kind `action`.
  - On `"cancel"`, it pays nothing.

  Either way it appends a `HumanMessage(name="confirmation", content=…)` saying what the handler pressed and what came of it, and clears `sign_off`. On Cancel the model then says it has not signed off and asks what the handler would like instead.
- **`turns.py`:**
  - `run_turn(env, body) -> None` and `answer(env, body) -> None`.
  - Both start with `app_api.who(token)`. If it is refused, the turn ends with no model call.
  - `run_turn` invokes `{"messages": [HumanMessage(body.message)], "model_calls": 0}`, and `answer` invokes `Command(resume=body.answer)`, each with `context=Asker(...)` and `thread_id=conversation_id`.
  - `_settle` ends the turn in one of three ways:
    - **Interrupted:** it calls `app_api.pause(token, turn_id, claim_id)`; the card comes from the app.
    - **Finished:** it calls `app_api.end(token, turn_id, last AI text, model_calls, cost)`.
    - **On an exception:** it calls `app_api.fail(token, turn_id, "<Type>: <message>")`, trimmed to 300 characters.

- [ ] **Step 1: Write the failing tests.**
  - `test_a_sign_off_pauses_and_reports_the_card_from_the_records`.
  - `test_a_fresh_copilot_on_the_same_saver_answers_and_pays_once`. It builds a second env and graph and resumes with `"sign_off"`. The app saw exactly one approve, and `list_claims` was not called again.
  - `test_cancel_pays_nothing_and_the_model_is_told`.
  - `test_marcus_over_the_limit_is_refused_without_a_pause`.
  - `test_a_refused_sign_in_ends_the_turn_before_any_model_call`.
  - `test_a_failed_turn_reports_why_and_the_next_message_works`.
- [ ] **Step 2: Run them.** Run `cd backend; uv run pytest tests/test_copilot_pause.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run them again.** Same command. Expected: PASS.
- [ ] **Step 5: Run the suite and commit.** Run `cd backend; uv run pytest -q`. Expected: all pass. Commit with `feat: the Copilot's loop works as the handler and asks before it pays`.

---

## Phase 3: Waiting, the success criteria and the memory (spec items 6, 9, 10)

### Task 8: Waiting in code, what needs a handler, and the criteria worked out in code

**Files:**
- Create: `backend/agent/copilot/waiting.py`, `backend/agent/copilot/criteria.py`
- Modify: `backend/agent/copilot/tools.py`
- Test: `backend/tests/test_copilot_waits.py`, `backend/tests/test_copilot_criteria.py`

**Interfaces (Produces):**
- **`wait_for_run(token, kind: Literal["dispatch","evaluation"], run_id, poll_s=3, limit_s=900, clock=time.monotonic, sleep=time.sleep) -> dict`** returns `{"status": "finished"|"failed"|"stopped"|"still_going", "run": <report>}`. It reads `/api/dispatches/{id}` or `/api/evaluations/{id}`, with the latter's `claims` dropped. When it is still going, it says where to follow the run: `/` for a dispatch, `/scoreboard/{id}` for an evaluation.
- **The tool `wait_for_run(run_id, kind)`** wraps it. `start_dispatch` and `start_evaluation` answer with the `run_id` they started.
- **The tool `what_needs_me()`** returns `{held_for_sign_off: [...], referred_or_left: [...], sent_back_not_worked: [...]}`:
  - `held_for_sign_off` comes from `/approvals`;
  - `referred_or_left` is the queue claims with decision `refer` or status `needs_handler`;
  - `sent_back_not_worked` is the claims with status `sent_back`.
- **`check_criteria(token, wait=wait_for_run) -> dict`** returns `{"rows": [{criterion, target, figure, met}], "all_met": bool | None, "evaluation": {"run_id", "status"} | {"refused": reason}}`:
  - It starts `held_out`, `tiny=False`, with `agents_at_once`, and waits.
  - Straight through is `measures.straight_through` against 0.70.
  - Accuracy is `measures.accuracy` against `objectives.accuracy`.
  - Over the limit is `signed_off == paid_over_limit`, from `/payouts/over-limit`. When both are 0 it is met, with `figure: "none paid over the limit yet"`.
  - When the evaluation was refused, failed, stopped or is still going, the first two rows have `figure: None, met: None` and `all_met` is `None`. The third row is always given.
- **The tool `check_success_criteria()`** wraps `check_criteria`, and the instructions tell the model to set out its rows as the table.

- [ ] **Step 1: Write the failing tests.**
  - Waits:
    - `test_the_wait_polls_every_3s_and_returns_when_finished`, with a fake clock and sleep, counting the reads and the model calls (none);
    - `test_after_15_minutes_it_is_still_going_with_where_to_follow`;
    - `test_a_refused_start_passes_the_reason_on`.
  - What needs a handler: `test_what_needs_me_after_a_dispatch`.
  - Criteria:
    - `test_criteria_from_measures_settings_and_the_count`;
    - `test_criteria_with_none_paid_over_the_limit`;
    - `test_criteria_after_a_failed_evaluation_give_only_the_third`;
    - `test_criteria_after_a_refused_start_give_only_the_third`;
    - `test_criteria_start_the_whole_held_out_set_with_agents_at_once`.
- [ ] **Step 2: Run them.** Run `cd backend; uv run pytest tests/test_copilot_waits.py tests/test_copilot_criteria.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run them again.** Same command. Expected: PASS.

### Task 9: The Copilot's memory of each handler

**Files:**
- Create: `backend/agent/copilot/memory.py`
- Modify: `backend/agent/copilot/graph.py` (`recall`, `save`)
- Test: `backend/tests/test_copilot_memory.py`

**Interfaces (Produces):**
- `open_copilot_store() -> AgentCoreMemoryStore | None` reads `UNDERCOVER_COPILOT_MEMORY_ID` each time it is called, in the AWS CLI's region.
- `recall(store, handler_id, message) -> list[str]` returns `store.search(("handlers", handler_id), query=message, limit=5)`.
- `save_turn(store, handler_id, conversation_id, message, answer)` puts two events at `(handler_id, conversation_id)`: the message as a `HumanMessage` and the answer as an `AIMessage`. Nothing else is saved.
- The `recall` node records a card of kind `memory` (`search_memory`, with the handler's message as its main input) with what it found, and puts the found notes in the system prompt. It is skipped silently, with no card, when the store is None.
- The handler id always comes from `who(token)`, never from the model or the message.

- [ ] **Step 1: Write the failing tests**, using `FakeMemoryStore` from `tests/fakes.py`.
  - `test_recall_searches_only_the_signed_in_handlers_namespace`.
  - `test_a_turn_saves_the_message_and_the_answer_and_nothing_else`. No tool output and no token appear in any saved value.
  - `test_danas_notes_are_never_used_for_marcus`.
  - `test_with_memory_off_there_is_no_memory_card_and_the_turn_works`.
- [ ] **Step 2: Run them.** Run `cd backend; uv run pytest tests/test_copilot_memory.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run them again.** Same command. Expected: PASS.
- [ ] **Step 5: Run the suite and commit.** Run `cd backend; uv run pytest -q`. Expected: all pass. Commit with `feat: the Copilot waits in code, checks the success criteria and remembers each handler`.

---

## Phase 4: The Copilot as an AgentCore app (spec: IT's choices, Deployment)

### Task 10: `agent/copilot/entrypoint.py`, `runtime/copilot.py`, and the dev and check scripts

**Files:**
- Create: `backend/agent/identity.py` (move `on_runtime` and `fetch_credentials(only: set[str] | None = None)` here from `agent/entrypoint.py`; the claim agent keeps fetching all of them), `backend/agent/copilot/payloads.py`, `backend/agent/copilot/entrypoint.py`, `runtime/copilot.py`
- Modify: `backend/agent/entrypoint.py` (import from `identity`), `scripts/dev.mjs` (the part `copilot` on 8081 with `--reload --reload-dir agent`), `scripts/check.mjs` (the staged import check also imports `runtime.copilot` and posts a health check), `agentcore/agentcore.json`, `runtime/policy.json`, `agentcore/cdk/lib/cdk-stack.ts` (`SETTINGS` gains `UNDERCOVER_COPILOT_MEMORY_ID`)
- Test: `backend/tests/test_copilot_entrypoint.py`

**Interfaces (Produces):**
- **`parse(payload)`** returns `Health | Turn | Answer`.
  - `Turn` has `conversation_id` (33–100 characters of `[A-Za-z0-9-]`), `turn_id: int`, `message: str` and `token: str`.
  - `Answer` has `conversation_id`, `turn_id`, `answer: Literal["sign_off","cancel"]` and `token`.
  - A bad payload answers 400. The error never echoes the token.
- **`build_copilot_app(make_settings=AgentSettings, inline=False) -> BedrockAgentCoreApp`.**
  - Nothing happens at import.
  - It gets ready on the first request, once, under a lock. On Runtime it calls `fetch_credentials(only={"OPENROUTER_API_KEY", "DATABASE_URL"})` and runs `random.seed()`. It then opens the checkpoints and the model.
  - `turn` and `answer` answer `{"accepted": true}` at once, and run `run_turn` / `answer` under `add_async_task` / `complete_async_task` on a daemon thread.
  - `health` answers `{"healthy": true}`.
  - It asks for no key.
- **`app = build_copilot_app()`** is the module-level app, for `uvicorn agent.copilot.entrypoint:app` and `runtime/copilot.py`.
- **`agentcore.json`** gains a second runtime, beside `agent`: `{"name": "copilot", "build": "CodeZip", "entrypoint": "copilot.py", "codeLocation": "runtime", "runtimeVersion": "PYTHON_3_12", "protocol": "HTTP", "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 300}, "instrumentation": {"enableOtel": false}, "additionalPolicies": ["policy.json"]}`.
- **`policy.json`'s `UseTheMemory`** also allows `memory/undercover_copilot_memory-*`.

- [ ] **Step 1: Write the failing tests.**
  - `test_health_answers_healthy`.
  - `test_a_turn_is_accepted_at_once_and_worked_as_an_async_task`, inline and with a stub model.
  - `test_on_runtime_it_fetches_only_two_credentials`, with `CREDENTIAL_UNDERCOVER_API_KEY_NAME` set too and `requires_api_key` patched. The key is never fetched.
  - `test_a_bad_payload_is_400_and_never_echoes_the_token`.
  - `test_importing_the_module_opens_nothing`.
  - `test_the_claim_agent_still_fetches_all_three`, which moves an existing test.
- [ ] **Step 2: Run them.** Run `cd backend; uv run pytest tests/test_copilot_entrypoint.py tests/test_entrypoint.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement it**, then edit the scripts and the platform files above. Make only the changes the second runtime needs.
- [ ] **Step 4: Run them again.** Same command. Expected: PASS.
- [ ] **Step 5: Run the full check.** Run `npm run check` with `.env` moved away. Expected: green. The staged runtime check prints both health answers.
- [ ] **Step 6: Commit** with `feat: the Copilot is an AgentCore app of its own, on the laptop and as a second runtime`.

---

## Phase 5: The sidebar (spec items 1, 5, 7)

### Task 11: The Copilot's API in the dashboard, and a fresh sign-in

**Files:**
- Create: `frontend/src/copilot/api.ts`
- Modify: `frontend/src/auth/AuthProvider.tsx` and `context.ts` (`getFreshToken()`)
- Test: `frontend/src/copilot/api.test.ts`, `frontend/src/auth/AuthProvider.test.tsx`

**Interfaces (Produces):**
- **`getFreshToken(): Promise<string | null>`** calls `supabase.auth.refreshSession()` when `session.expires_at * 1000 - Date.now() < 20 * 60_000`, then returns the access token.
- **In `copilot/api.ts`:**
  - `createConversation()`, `listConversations()`, `getConversation(id)`;
  - `sendMessage(id, text)` and `answerConfirmation(id, answer)`, both of which call `getFreshToken()` first;
  - the types `Turn`, `Step` and `ConfirmationCard`, matching Task 2.

- [ ] **Step 1: Write the failing tests.**
  - `renews a session with less than 20 minutes left before sending`.
  - `does not renew a fresh session`.
  - `a 409 surfaces its reason`.
- [ ] **Step 2: Run them.** Run `cd frontend; npx vitest run src/copilot src/auth`. Expected: FAIL.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run them again.** Same command. Expected: PASS.

### Task 12: The sidebar

**Files:**
- Create in `frontend/src/copilot/`:
  - `Sidebar.tsx`, `ConversationView.tsx`, `StepCard.tsx`, `ConfirmationCard.tsx`, `Markdown.tsx`, `icons.tsx`, `useConversation.ts`;
  - `copilot.css`, which uses only `tokens.css` variables, light and dark.
- Modify: `frontend/src/App.tsx` (the layout: page plus sidebar), `frontend/package.json` (`react-markdown`, `remark-gfm`)
- Test: `frontend/src/copilot/Sidebar.test.tsx`, `StepCard.test.tsx`, `ConfirmationCard.test.tsx`, `Markdown.test.tsx`

**Interfaces (Produces):**
- **Opening and closing.** The sidebar is open by default. Collapsing it leaves a slim tab with the Copilot's icon. The choice is kept in `localStorage['copilot.open']`. Below **1100 px** wide it overlays the page instead of sitting beside it.
- **`useConversation(id)`** polls `getConversation` every **1000 ms** only while the last turn is `working`, and stops when the turn is idle, waiting, done, failed or stopped.
- **Messages.** The handler's messages sit on the right and the Copilot's answers on the left. Answers render through `Markdown`, which also links each `CLM-\d{4}` to `/claims/<id>`.
- **The cards.** Each card shows an icon for its kind, the tool name and its main input, and its state: a spinner with live seconds while working, the time taken when done, or the reason when refused or failed. A card enters with a 150 ms fade and rise, which `prefers-reduced-motion` turns off. Clicking a card opens its input and output, trimmed, and for a context-layer card its file and owner. Action cards carry the accent border.
- **While the Copilot works,** a working indicator sits below the last card.
- **The cost line.** Under a finished answer, in small print: `N model calls · $0.0123 · 14 s`.
- **The confirmation card** shows the claim, the customer, the amount and the reason, with the buttons "Sign off and pay" and "Cancel". While it waits, the message box's placeholder is "Answer the confirmation above first", and sending is disabled.
- **An empty conversation** says "Hi <first name>" and offers 3 suggested questions to click.
- **The top** has "New conversation" and the earlier conversations, newest first, each titled with its first question.
- **The foot** has the "Ask the Copilot" box (Enter sends, Shift+Enter starts a new line, disabled while a turn works) and "Signed in as <name>".
- **A failed turn** shows its error in a short message. A stopped turn shows "This turn stopped". Either way a new message can be sent.

- [ ] **Step 1: Write the failing tests.**
  - `opens on first sign-in and remembers it was collapsed`.
  - `polls every second while working and not when idle` (fake timers).
  - `shows a working card with seconds and a done card with its time`.
  - `opens a card to show input, output, file and owner`.
  - `an action card stands out`.
  - `the confirmation card disables the box until answered and sends sign_off`.
  - `cancel sends cancel`.
  - `Enter sends, Shift+Enter adds a line, disabled while working`.
  - `markdown renders a table and links CLM-1012`.
  - `greets by first name with suggestions`.
  - `a failed and a stopped turn let a new message through`.
- [ ] **Step 2: Run them.** Run `cd frontend; npx vitest run src/copilot`. Expected: FAIL.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run them again.** Same command, then `npm --prefix frontend run lint` and `npx --prefix frontend tsc -b`. Expected: PASS.

### Task 13: End-to-end tests with the stand-in Copilot

**Files:**
- Create: `e2e/copilot_stand_in.py` (the real `agent.copilot.entrypoint` app with `agent_provider=scripted`, on port **8093**), `e2e/copilot.spec.ts`
- Modify: `e2e/playwright.config.ts` (a web server for the stand-in; the backend's `UNDERCOVER_COPILOT=http://127.0.0.1:8093`)

**Interfaces (Consumes):** `ScriptedCopilotModel` (Task 6), the routes (Task 2), and the sidebar (Task 12).

- [ ] **Step 1: Write the failing e2e tests.**
  - `the sidebar is open on every page, collapses to a tab and stays collapsed after a reload`.
  - `a scripted turn shows a lookup, an action, a refusal and a formatted answer with a claim link`.
  - `a sign-off shows the confirmation card, pays on "Sign off and pay", and the Approvals page shows it paid`.
  - `a narrow window shows the sidebar over the page`.
- [ ] **Step 2: Run them.** Run `npm run check`. Expected: the new tests FAIL, and the existing e2e tests still pass with the sidebar open.
- [ ] **Step 3: Implement the stand-in and the config.**
- [ ] **Step 4: Run the full check.** Run `npm run check` with `.env` moved away. Expected: green.
- [ ] **Step 5: Commit** with `feat: the Copilot's sidebar on every page, with its cards and the confirmation before a payout`.

---

## Phase 6: Docs and live checks

### Task 14: Docs, the clean check, then the live runs

**Files:** `docs/SPEC.md` (the three success criteria, and the Copilot), `docs/TECH_STACK.md` (the Copilot part, the new routes, Deployment: the second runtime, its settings, `undercover_copilot_memory` with its two strategies, and `UNDERCOVER_COPILOT` / `UNDERCOVER_COPILOT_RUNTIME`), `docs/CODE_STANDARDS.md` (the Copilot as a part of its own, under the same rules), `docs/PROCESS.md` (five parts in `npm run dev`; the Copilot on 8081; its health check through `runtime/copilot.py`), `docs/STATUS.md`.

- [ ] **Step 1: Update the docs.** Then run `npm run check` in a clean `git worktree`, where `.env` does not exist. Expected: green.
- [ ] **Step 2: Run the health check through the Runtime file.** Run `$env:PYTHONPATH = "backend"; uv run --directory . --project backend --env-file .env --isolated --only-group runtime python -m runtime.copilot`, then post `{"operation":"health"}` to `http://127.0.0.1:8081/invocations`. Expected: `{"healthy": true}`.
- [ ] **Step 3: Reseed once and start the parts.** Run `npm run seed`, then `npm run dev` on the default model, on the direct route, with memory off. Sign in as Dana Whitfield through `e2e/live_security.mjs` (new steps `copilot:<message>` and `confirm:sign_off`), and ask, in one conversation:
  1. "Work the queue with 10 agents and tell me what needs me."
  2. "Why does CLM-… need me?", about one of the payouts held for sign-off.
  3. "Send it back with a note …"
  4. "Sign off on another one", then press Sign off and pay.
  5. "Are we meeting the success criteria we agreed?"

  Check every figure in answer 5 against the Scoreboard and the claims' pages.
- [ ] **Step 4: Pick the conversation up after a restart.** Restart `npm run dev`, reopen the conversation, and send one more message. Expected: it has all of the conversation.
- [ ] **Step 5: Check Marcus's refusal.** Signed in as Marcus Oyelaran, ask "Sign off CLM-…" for a payout over the limit. Expected: it is declined with the reason, and no card appears.
- [ ] **Step 6: Check the sidebar's look.** Check the sidebar in light and dark mode through the Playwright MCP server. Take screenshots and read them back.
- [ ] **Step 7: Record the results and finish.** Write the results and what the runs spent in `docs/STATUS.md`. Move this plan to `docs/plans/`. Commit with `docs: the Copilot's live checks; the plan is done`.
