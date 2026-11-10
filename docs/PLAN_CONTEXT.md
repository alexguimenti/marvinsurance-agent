# The context layer, memory and the Gateway: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the claim agent's tools onto the Gateway (or the same tools directly), serve the business's context as its own MCP server, make every decision record meet claims manual section 14, give the agent a memory of each customer, and show every step on the claim page.

**Architecture:** The agent reaches the app and the context layer through one of two routes, chosen when a claim graph is built: `agent/direct.py` (the app with the key, the context layer over unsigned MCP) or `agent/gateway.py` (both over the Gateway's MCP, signed with SigV4). The context layer is a new package, `backend/context/`, a stateless FastMCP server over seven files that data governance owns. The app keeps the latest payout calculation per claim attempt and refuses a decision record that falls short of section 14. Memory is an `AgentCoreMemoryStore` behind a small module, used by a graph node of its own and a `search_memory` tool.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy Core, LangGraph, `mcp` 1.x (FastMCP server and client), `langgraph-checkpoint-aws` (AgentCore Memory), boto3/botocore (SigV4), Mangum, PyYAML; React + Vite + Vitest; Playwright.

**Spec:** `docs/CONTEXT.md`

SIDENOTE: For consistency with the course, please read `labs/week3day3.ipynb`, which deploys the app and the context layer, creates the Gateway and the memory, and runs the agent against them, without running it. Where the notebook and this document differ, follow this document. Include this sidenote in your plan too. (In this repo the notebook is at `../undercover/labs/week3day3.ipynb`.)

## Global Constraints

- Where our code lives: the Python root is `backend/` (`app/`, `agent/`, `tests/`, `pyproject.toml`); the dashboard is `frontend/` (the spec's `dashboard/src` is `frontend/src`).
- The seven files in `context/` are data governance's: copy them byte for byte from `../undercover/context/` and never edit them.
- Gateway tool names are `<target>___<operation>`; targets are exactly `undercover-app` and `undercover-context`.
- `UNDERCOVER_GATEWAY_URL`, `UNDERCOVER_CONTEXT` (default `http://127.0.0.1:8006/mcp`) and `UNDERCOVER_MEMORY_ID` are read when a claim graph is built, never at import.
- SigV4 signs for the service `bedrock-agentcore`, with the credentials and region of `boto3.Session()` (the AWS CLI's).
- The agent stays synchronous and works on Windows; no async bridge may change the calling thread's event loop.
- Every operation and every context tool: an `operation_id` (tools: their function name), a one-line docstring, no optional inputs.
- Section 14 refusals are HTTP 422 whose `detail` is a string that starts `Claims manual section 14: `.
- The claim agent's API document has six operations: `get_claim`, `get_policy`, `get_claim_history`, `calculate_payout`, `record_decision`, `pay_claim`.
- Tests never call a model, AWS, Supabase Auth or the Gateway; `npm run check` never reads `.env`.
- Commit at the end of each phase (spec, Planning). Do the live checks last, after one `npm run seed`.

## Review Focus

1. **The main thread's event loop after a tool call.** A bridge built on `asyncio.run` leaves the main thread with no loop, and the Mangum tests that run later then fail. After any MCP call, the loop that was set must still be set and open. Task 1 tests this.
2. **A calculation from an earlier attempt.** After a failed attempt, the calculation from that attempt must not satisfy section 14 for the next attempt. Task 6 tests this.
3. **A payout that differs only by float noise.** An approval of 420.00 against a calculation of 419.999999 must match. Both values are compared rounded to cents. Task 6 tests this.
4. **A claim worked on again after a restart.** It saves to memory once, and `search_memory` never returns the summary of the claim being worked. Task 7 tests this.
5. **An unknown section or record name sent to the context layer.** The model gets a refusal and the claim goes on; it never crashes. Task 3 (server) and Task 4 (agent) test this.

## Rulings made while planning (the code differs from the spec's notes)

- **Where `context/` lives.** It is at `backend/context/`, so `cd backend; uv run -m context` works like `uv run -m agent`. The Lambda zip still holds it as `context/`. *If wrong:* it costs moving one folder.
- **No plans table.** The app needs no plans: our `calculate_payout` takes the limits and the deductible as inputs, and the only app reader of `plans.json` is the `get_plan` route, which moves to the context layer. Seeding therefore loads only the documents. The product stops reading `plans.json`, as the spec asks. *If wrong:* it costs one table plus seeding.
- **The claim agent's instructions.** They still end with the numbered section index of both documents (ours does not append the full contents). The index now comes from the documents table through `GET /api/agent/documents/{document}`. *If wrong:* the prompt changes only.
- **The MCP client.** Both routes use the `mcp` package's client (`streamablehttp_client` + `ClientSession.call_tool`) instead of `langchain-mcp-adapters`. An `isError` result is the app's or the context layer's refusal, and an `McpError` is the Gateway's. This needs one fewer dependency and avoids the `handle_tool_errors` trap.
- **Field names.** Decision fields keep our names `rationale` and `citations` (the spec's "reason" and "section"). The new fields use the data dictionary's names: `decided_by`, `decided_at`, `calculation` and `manual_version`.
- **The model's decision tool.** `save_decision` becomes `record_decision`, matching the operation's name. It calls the app, so a section 14 refusal goes back to the model. On success it ends the work through the graph's state.
- **The prefetched facts stay.** `get_claim`, `get_policy`, `get_claim_history` and the policy's `get_plan` are still handed over as tool results, on whichever route is in use.
- **When the new step kinds start.** Each one is recorded from the phase that creates it: `refusal` in phase 1, `lookup` in phase 2, `memory_search`/`memory_save` in phase 4. Phase 5 is the dashboard, the Gateway's names on tool calls and the claim API's `context_used`.
- **Brand name.** The context files say "UnderCover" and our data says "MarvInsurance". The files are governance's and stay as they are. `decided_by` reads "UnderCover Agent", as the spec says.

---

## Phase 1: Tools from the Gateway (spec step 1)

### Task 1: The two routes and the MCP bridge

**Files:**
- Create: `backend/agent/mcp_bridge.py`, `backend/agent/direct.py`, `backend/agent/gateway.py`, `backend/agent/route.py`
- Modify: `backend/pyproject.toml` (add `mcp`, `boto3` to dependencies), `backend/uv.lock`
- Test: `backend/tests/test_routes.py`, `backend/tests/test_mcp_bridge.py`; stand-in in `backend/tests/fakes.py`

**Interfaces:**
- Produces:
  - `mcp_bridge.call_tool(url: str, name: str, args: dict, auth: httpx.Auth | None = None) -> str`. It opens a session for this one call, on a new event loop that it closes afterwards; `asyncio.run` is never used and the thread's loop is never set. It returns the text of the result's content. When `isError` is set it raises `ToolRefused(text)`; an `McpError` propagates.
  - `mcp_bridge.ToolRefused(Exception)`
  - `gateway.SigV4Auth(httpx.Auth)`, with `__init__(self, session: boto3.Session, service: str = "bedrock-agentcore")`
  - `gateway.GatewayRefused(Exception)`
  - `route.APP_TOOLS = ("get_claim", "get_policy", "get_claim_history", "calculate_payout", "record_decision", "pay_claim")`
  - `route.CONTEXT_TOOLS: tuple[str, ...]`, empty in this task and filled in Task 4
  - `class Route(Protocol)`, with:
    - `kind: Literal["direct", "gateway"]`
    - `app(self, operation: str, args: dict) -> Any`, which returns the parsed JSON
    - `context(self, tool: str, args: dict) -> Any`
    - `shown_name(self, tool: str) -> str`
  - `route.make_route() -> Route`, which reads the environment each time it is called
  - `direct.DirectRoute`, which calls the app through `agent.api`. Refusals stay as they are today: `Refused` and `LookupError`.
  - `gateway.GatewayRoute(url: str, auth: httpx.Auth, call=mcp_bridge.call_tool)`:
    - The name it calls is `f"undercover-app___{op}"` or `f"undercover-context___{tool}"`.
    - An `McpError` becomes `GatewayRefused(str(err))`.
    - A `ToolRefused` stays as it is.
    - `shown_name` gives the Gateway's name.

- [x] **Step 1: Write the failing tests.** In `test_mcp_bridge.py`, start a FastMCP server in a thread on a free port, using uvicorn on `streamable_http_app()` with two tools: `echo(text)` and `boom()`, which raises.
  - `test_a_call_returns_the_tools_text`: `call_tool(url, "echo", {"text": "hi"}) == "hi"`.
  - `test_a_tool_error_is_tool_refused_with_its_message`
  - `test_the_main_threads_loop_is_left_as_it_was`: set a loop, call twice, then assert `asyncio.get_event_loop() is loop` and `not loop.is_closed()`.

  In `test_routes.py`, add a `StandInGateway(backend_client)` in `fakes.py`. It is a `call` function that:
  - strips `undercover-app___` and forwards to the test backend with the key;
  - turns a 4xx into `ToolRefused(detail)`;
  - raises `McpError(ErrorData(code=-32002, message="Tool Execution Denied: ..."))` for `undercover-app___pay_claim` when the amount is over 1000;
  - records every name it is called with.

  Tests:
  - `test_both_routes_return_the_same_answer_for_each_app_tool`: parametrize over `get_claim`, `get_policy`, `get_claim_history`.
  - `test_the_gateway_route_calls_tools_by_the_gateways_names`: the names it records are `["undercover-app___get_claim", ...]`.
  - `test_an_app_refusal_on_each_route`: `Refused` on the direct route, `ToolRefused` on the gateway route; same message.
  - `test_a_policy_refusal_is_gateway_refused`
  - `test_make_route_reads_the_environment_when_called`: set `UNDERCOVER_GATEWAY_URL` after import; then `make_route().kind == "gateway"`, with `SigV4Auth` stubbed.
  - `test_sigv4_signs_for_bedrock_agentcore`: sign a request with fixed fake credentials; the `Authorization` header contains `/bedrock-agentcore/aws4_request`.
- [x] **Step 2: Run them.**
  - Run: `cd backend; .venv\Scripts\python -m pytest tests/test_mcp_bridge.py tests/test_routes.py -q`
  - Expected: FAIL, with `ModuleNotFoundError` (agent.mcp_bridge).
- [x] **Step 3: Implement.**
  - `uv add mcp boto3`.
  - Write the four modules to the interfaces above.
  - Add `UNDERCOVER_GATEWAY_URL`, `UNDERCOVER_CONTEXT` and `UNDERCOVER_MEMORY_ID` to `OUTSIDE_WORLD` in `conftest.py`. Add the same three, plus `AWS_PROFILE`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_SESSION_TOKEN`, to the list `scripts/check.mjs` clears.
- [x] **Step 4: Run them again.**
  - Run: the same command.
  - Expected: PASS.

### Task 2: The claim graph on a route; refusals; a payment the Gateway refused

**Files:**
- Modify:
  - `backend/agent/tools.py`: `build_tools(route)`; `get_customer_history` becomes `get_claim_history`.
  - `backend/agent/graph.py`, `backend/agent/worker.py` (`_build` calls `make_route()`), `backend/agent/events.py` (the `refusal` step), `backend/agent/api.py` (`gateway_refused`).
  - `backend/agent/scripted.py`, `backend/agent/instructions_claim.md` (the tool name).
  - `backend/app/agent_routes.py`, `backend/app/claims.py`, `backend/app/db.py` (`payments.hold_reason` Text), `backend/app/api.py` (`payment_view` shows `hold_reason`).
  - `frontend/src/api.ts`, `frontend/src/pages/Approvals.tsx`, `frontend/src/pages/ClaimDetail.tsx`.
- Test: `backend/tests/test_graph_routes.py`, `backend/tests/test_agent_api.py`, `frontend/src/pages/Approvals.test.tsx`

**Interfaces:**
- Consumes: Task 1's `Route`, `make_route`, `GatewayRefused`, `ToolRefused`.
- Produces:
  - **The app operation:** `POST /api/agent/claims/{claim_id}/gateway-refused`, with `operation_id="hold_after_gateway_refusal"` and key only. It moves only a claim whose status is `in_progress`, whose decision is `approve` and that has no paid payment. It puts the payment on hold with `hold_reason="gateway_refused"` and the claim to `awaiting_approval`, then returns `{"first_time": bool}`. Any other claim gets a 409. It is not in `GATEWAY_OPERATIONS`.
  - **The agent's call:** `api.gateway_refused(claim_id) -> bool`.
  - **The hold reason:** `hold_for_sign_off` writes `hold_reason="over_limit"`.
  - **The `refusal` step:** `{"by": "app" | "context" | "gateway", "tool": <shown name>, "reason": str}`. A `ToolMessage` with `status == "error"` is recorded as a `refusal` step, never as a `tool_result`.
  - **What the model sees:** a Gateway refusal reaches the model as `"Error: refused by the Gateway: <message>"`; any other refusal is `"Error: <message>"`, as today.

- [x] **Step 1: Write the failing tests** in `test_graph_routes.py`, using `agent_env` with an injected route (add a `route=` parameter to `agent_env`/`open_env`). Each test runs on both `DirectRoute` and `GatewayRoute(call=StandInGateway)`.
  - `test_a_claim_is_decided_on_either_route`: CLM-1001, approve 420, paid.
  - `test_an_app_refusal_reaches_the_model_and_is_a_refusal_step`: the stub model calls `get_claim_history`; the backend is patched to 404; the next model input contains `"Error:"`; a step `refusal` with `by == "app"`.
  - `test_a_gateway_refusal_of_a_tool_is_recorded_as_the_gateways_and_work_goes_on`: StandInGateway denies `calculate_payout` once; the step `refusal` has `by == "gateway"`; the claim is still decided.
  - `test_a_payment_the_gateway_refused_waits_for_sign_off_and_a_sign_off_pays_it`: raise the approval limit setting to 5000 and approve 1200. Then:
    - the status is `awaiting_approval`;
    - `payment.hold_reason == "gateway_refused"`;
    - `sign_off` by HND-01 makes it `paid`;
    - `carry_on` finishes.
  - `test_get_claim_text_is_the_same_on_both_routes_and_is_what_the_check_reads`

  In `test_agent_api.py`:
  - `test_gateway_refused_moves_only_an_approved_unpaid_claim_in_progress`: a decline gets 409, a paid claim gets 409, and with no key it is 401.
  - `test_gateway_refused_is_not_in_the_agent_openapi`

  In `Approvals.test.tsx`:
  - `shows that the Gateway refused the payment`: the row text is "The Gateway refused this payment" when `hold_reason === 'gateway_refused'`, and "Over the approval limit" otherwise.
- [x] **Step 2: Run them.**
  - Run: `.venv\Scripts\python -m pytest tests/test_graph_routes.py tests/test_agent_api.py -q` and `cd ../frontend; npx vitest run src/pages/Approvals.test.tsx`
  - Expected: FAIL.
- [x] **Step 3: Implement.**
  - **Tools:** `build_tools(route)` wraps every app call in `route.app(...)`. The model gives no ids: `claim_id` stays injected from state.
  - **`pay_node`:** it calls `route.app("pay_claim", {...})`. On `GatewayRefused`, it records a `refusal` step (`by="gateway"`), calls `api.gateway_refused`, and records `status awaiting_approval` the first time. It then interrupts in the existing loop.
  - **Bookkeeping stays on `agent.api`, direct:** the screening's read and referral, the steps, the model calls, take, fail and hold.
  - **The tool node's error handler** formats the two messages above.
  - **`emit_message`** records the `refusal` step. `by` is `"gateway"` when the content starts `"Error: refused by the Gateway"`; otherwise it is the tool's target (app or context).
- [x] **Step 4: Run them again, then the whole backend suite.**
  - Run: `.venv\Scripts\python -m pytest -q > ..\.superpowers\phase1.log` and read the tail.
  - Expected: all pass. Existing tests that name `save_decision`/`get_customer_history` are updated to the new tool names only.
- [x] **Step 5: Check by hand.** With `npm run dev` running and no `UNDERCOVER_GATEWAY_URL`, run `cd backend; uv run -m agent CLM-1001`.
  - Expected: `CLM-1001: approve $420.00, paid` (or the model's own decision, as before).
- [x] **Step 6: Commit the phase:** `git commit -m "feat: the claim agent's tools on the Gateway or direct, one module per route"`

---

## Phase 2: The context layer (spec step 2)

### Task 3: The context layer's server

**Files:**
- Create:
  - `backend/context/` with the seven files, copied unchanged from `../undercover/context/`.
  - `backend/context/__init__.py`, `backend/context/__main__.py`, `backend/context/files.py`, `backend/context/server.py`.
- Modify:
  - `backend/pyproject.toml`: add `pyyaml` to dependencies; add a `context` group with exactly `mcp`, `mangum` and `pyyaml`; set `packages = ["app", "agent", "context"]`; add `context = ["*.md", "*.yaml"]` to the package data.
  - `scripts/check.mjs`: a new step, "Context layer imports on a server", that runs `uv run --project backend --isolated --only-group context python -c "__import__('context.server')"` in `backend`.
  - `scripts/dev.mjs`: a fourth part, `context`, that runs `python -m context` in `backend`.
- Test: `backend/tests/test_context_server.py`, `backend/tests/test_context_lambda.py`

**Interfaces:**
- Produces:
  - **The files**, read once at import in `files.py`:
    - `SOURCES: dict[str, dict]` (file → `{"file", "system", "owner", "reviewed"}`)
    - `GLOSSARY`, `FIELDS`, `PLANS`, `RULES`, `REFERENCE`
    - `SECTIONS: list[Section]`, where `Section(document, number, title, text, file)`; the front matter is never part of a section.
    - `split_front_matter(text) -> tuple[dict, str]`
  - **Tool names:** `lookup_term`, `describe_fields`, `get_plan`, `get_claim_rules`, `search_reference_data`, `search_documents`, `read_section`.
  - **Every answer** is `{"answer": <payload>, "sources": [<source>, ...]}`:
    - `search_documents`: each hit is `{"document", "section", "title", "snippet", "file"}`, and `sources` lists each file once.
    - `read_section(document: str, section: int)`: same inputs as the app's operation today.
    - An unknown term gets `answer = {"unknown_term": term, "terms": [...]}`.
    - An unknown record, plan, document or section raises `ValueError` (an `isError` result).
  - **Building the server:** `server.build_server(lambda_host: bool = False) -> FastMCP`, with `stateless_http=True`, `json_response=True`, and every tool marked `readOnlyHint=True`. When `lambda_host` is true it passes `TransportSecuritySettings(enable_dns_rebinding_protection=False)`.
  - **The Lambda handler:** `server.handler(event, context)`, which runs `Mangum(build_server(True).streamable_http_app(), lifespan="auto")(event, context)` with a new server for each event.
  - **On the laptop:** `python -m context` serves uvicorn at `127.0.0.1:8006`, path `/mcp`.

- [x] **Step 1: Write the failing tests** against the real files. Call the tools in process with `await build_server().call_tool(...)` through a small sync helper, or through `mcp_bridge.call_tool` against a server in a thread.
  - `test_lookup_term_matches_any_case_and_names_its_file`: `lookup_term("CARRIER")` gives `answer.sections == [12]`, and `sources[0]` has `file == "glossary.yaml"` and `system == "The policy wording, version 2026.1"`.
  - `test_an_unknown_term_gets_the_glossarys_terms`
  - `test_describe_fields_for_each_record`: parametrize `claim`, `policy`, `previous_claim`; an unknown record is refused.
  - `test_get_plan_has_limits_deductible_what_it_applies_to_and_add_ons`: premium; `deductible_applies_to == ["baggage", "emergency_medical"]`.
  - `test_get_claim_rules`: `filing_window.days == 90`.
  - `test_search_reference_data_needs_every_word_any_case`: `"lisbon STRIKE"` finds the Portugal fact with `topic == "events"`, and `"lisbon hurricane"` finds nothing.
  - `test_search_documents_names_each_hits_file`
  - `test_read_section_of_claims_manual_14`: the title is "Decision records" (whatever the file says), and the text holds no front matter.
  - `test_a_section_that_does_not_exist_is_refused`
  - `test_every_tool_has_a_one_line_description_no_optional_inputs_and_is_read_only`
  - `test_the_files_are_read_once`: patch `Path.read_text` after import, call every tool, and assert it is never called.

  In `test_context_lambda.py`:
  - `test_the_handler_lists_then_calls_twice_in_one_process`. Use two Function URL events (payload 2.0, `host: abc.lambda-url.us-east-1.on.aws`, POST `/mcp`, `Accept: application/json, text/event-stream`): `tools/list`, then `tools/call lookup_term`. Both answer 200 with JSON-RPC results.
- [x] **Step 2: Run them.**
  - Run: `.venv\Scripts\python -m pytest tests/test_context_server.py tests/test_context_lambda.py -q`
  - Expected: FAIL (no module `context`).
- [x] **Step 3: Implement.** Copy the files with `cp ../../undercover/context/* context/`, check them with `git diff --no-index`, and write the modules.
- [x] **Step 4: Run them again.**
  - Expected: PASS. Then run `node scripts/check.mjs` up to the new step, or that step alone.
  - Expected: "Context layer imports on a server" passes.

### Task 4: The app reads the documents from its table; the agent reads the context layer

**Files:**
- Modify:
  - `backend/app/db.py`: a `documents` table, with `document` (Text, primary key), `version` (Text), `source` (JSON text) and `text` (Text).
  - `backend/app/documents.py`: sections from the table, split as today, with the version from the front matter.
  - `backend/app/seed.py` (`seed_documents`, which parses the front matter with PyYAML), `backend/app/config.py` (`context_dir`, default `backend/context`).
  - `backend/pyproject.toml`: add `pyyaml` to the `server` group, since `app.seed` imports it.
  - `backend/app/reference.py`: drop `plans.json`, `policy_wording.md` and `claims_manual.md` from `ALLOWED_FILES`; drop `plan`, `add_ons` and `manual`.
  - `backend/app/api.py`: remove `search_documents`/`read_section`; `GATEWAY_OPERATIONS` becomes the six.
  - `backend/app/agent_routes.py`: remove `plan` and `knowledge`; `GET /api/agent/documents/{document}` reads the table.
  - `backend/app/claims.py`: versions from the table.
  - `backend/agent/route.py` (`CONTEXT_TOOLS`), `backend/agent/direct.py` (context over `mcp_bridge` at `UNDERCOVER_CONTEXT`), `backend/agent/tools.py`.
  - `backend/agent/prompts.py`: the index comes from `api.list_sections`, so there is no `app.documents` import in the agent.
  - `backend/agent/events.py` (the `lookup` step), `backend/agent/scripted.py`, `backend/agent/instructions_claim.md`.
  - `e2e/playwright.config.ts`: a `context` webServer on port 8016; the agent's env gets `UNDERCOVER_CONTEXT=http://127.0.0.1:8016/mcp`.
- Test: `backend/tests/test_documents.py`, `backend/tests/test_seed.py`, `backend/tests/test_agent_openapi.py`, `backend/tests/test_tools.py`, `backend/tests/test_lambda.py`

**Interfaces:**
- Consumes: Task 3's tool names and answer shape; Task 1's routes.
- Produces:
  - **Seeding:** `seed_documents(engine, context_dir: Path) -> None`, run by `seed_claims`. It is insert-or-ignore, so a reseed rebuilds it.
  - **Reading:** `documents.sections(engine, document) -> list[Section]` and `documents.version(engine, document) -> str`.
  - **The agent's model tools:**
    - from the app: `get_claim`, `get_policy`, `get_claim_history`, `calculate_payout`, `save_decision` (until Task 6);
    - from the context layer: `lookup_term`, `describe_fields`, `get_plan`, `get_claim_rules`, `search_reference_data`, `search_documents`, `read_section`.
  - **Gone:** `list_sections` and `lookup_knowledge`.
  - **Still prefetched:** `get_claim`, `get_policy`, `get_claim_history`, and `get_plan(policy["plan"])` from the context layer.
  - **`read_section`** keeps `MAX_SECTIONS` and the "already read" rule on the agent's side.
  - **The `lookup` step:** `{"tool": <shown name>, "args": dict, "sources": [source...], "found": str}`, with at most 200 characters in `found`. It is recorded for every successful context tool result, including prefetched ones, and takes the place of today's section-summary `tool_result`.
  - **`agent.tools.claim_context`:** each context tool's answer goes to the model as the JSON text of `answer` plus `sources`.

- [x] **Step 1: Write the failing tests.**
  - `test_seed_loads_both_documents_with_their_front_matter_version`: `version(engine, "claims_manual") == "2026.2"`, and no section text starts with `---`.
  - `test_the_app_never_reads_plans_or_the_documents_from_data`: build the app on a copy of `data/` without `plans.json`, `policy_wording.md` and `claims_manual.md`; then seed, `GET /api/claims/CLM-1001`, and `POST` a decision; all succeed.
  - `test_the_agent_openapi_has_the_six_operations`: update the existing test to exactly the six names.
  - `test_the_app_package_imports_nothing_from_context`: extend `test_the_app_imports_nothing_from_the_agent` with `context`.
  - In `test_tools.py`, against a stand-in context route (`FakeContext` in `fakes.py`, which serves the real `context.files` in process):
    - `test_each_context_tool_answers_on_the_direct_route`
    - `test_on_the_gateway_route_context_tools_are_called_undercover_context___name`
    - `test_an_unknown_section_is_a_refusal_the_model_sees`
    - `test_a_lookup_step_names_the_file_and_its_source`
  - `test_the_claim_prompt_ends_with_the_section_index_from_the_table`
- [x] **Step 2: Run them.**
  - Run: the files above.
  - Expected: FAIL.
- [x] **Step 3: Implement** to the interfaces. Then:
  - Update `instructions_claim.md`'s tool names (no other wording yet; Task 7 rewrites the goals).
  - Change the scripted model to read `read_section` from the context layer, unchanged in name.
  - Leave `data/plans.json`, `data/policy_wording.md` and `data/claims_manual.md` where they are.
- [x] **Step 4: Run the whole backend suite and the e2e.**
  - Run: `.venv\Scripts\python -m pytest -q > ..\.superpowers\phase2.log`, then `cd ../e2e; npm test`.
  - Expected: all pass.
- [x] **Step 5: Check by hand.** With `npm run dev` running (now four parts), call `lookup_term("carrier")` and `read_section("claims_manual", 14)` through `DirectRoute().context(...)` from a one-off python line.
  - Expected: both answer, with sources.
- [x] **Step 6: Commit the phase:** `git commit -m "feat: the context layer as its own MCP server; the app's documents from its table"`

---

## Phase 3: A complete decision record (spec step 3)

### Task 5: The calculation on file, and section 14's checks

**Files:**
- Modify:
  - `backend/app/db.py`:
    - New table `payout_calculations`, with `claim_id` (Text, primary key), `attempt` (Integer), `payout` (Float), `steps` (JSON text) and `created_at`.
    - New `decisions` columns: `decided_by` (Text, not null) and `manual_version` (now not null). `created_at` is shown as `decided_at`.
  - `backend/app/api.py`:
    - `calculate_payout` becomes `POST /api/claims/{claim_id}/payout` (shared guard, same body as `PayoutBody`). It stores the result for the attempt `claims.attempts + 1` and returns it.
    - `DecisionIn` drops `calculation` and gains `decided_by: str`.
    - `_decision` shows `calculation` (`{"lines": steps, "payout": ...}` or `None`), `decided_by`, `decided_at` and `manual_version`.
  - `backend/app/claims.py`: `record_decision` writes the calculation on file for an approval.
  - New `backend/app/section14.py`, with `request_problems(body: DecisionIn) -> str | None` and `calculation_problem(engine, claim_id, decision, payout) -> str | None`.
- Test: `backend/tests/test_section14.py`

**Interfaces:**
- Produces:
  - **The messages,** pinned exactly:
    - `"Claims manual section 14: the reason is blank."`
    - `"Claims manual section 14: the section relied on is blank."` (`citations` empty or only blank strings)
    - `"Claims manual section 14: who made the decision is blank."`
    - `"Claims manual section 14: an approval pays more than zero."`
    - `"Claims manual section 14: a decline or a referral pays nothing."`
    - `"Claims manual section 14: an approval needs a payout calculation on file for this attempt. Call calculate_payout first."`
    - `"Claims manual section 14: the payout ${p:,.2f} does not match the calculation on file (${c:,.2f})."`
  - **When each check runs:** the request checks run before the claim is looked up (the same answer on an unknown claim). The calculation check compares `round(x, 2)`.
  - **For tests:**
    - `tests/fakes.py: record_approval(client, claim_id, payout, decided_by="Test") -> Response`. It posts the calculation `{"items": [{"description": "Payout", "amount": payout, "item_limit": None}], "third_party_paid": 0, "deductible": 0, "benefit_max": None}`, then the decision.
    - `tests/fakes.py: write_calculation(engine, claim_id, payout)` for direct writes.

- [ ] **Step 1: Write the failing tests** in `test_section14.py`.
  - `test_each_request_check_is_a_422_naming_section_14`: parametrize the five request messages, each on an unknown claim `CLM-0000` as well as CLM-1001.
  - `test_an_approval_with_no_calculation_on_file_is_refused`
  - `test_an_approval_that_differs_from_the_calculation_is_refused`
  - `test_a_calculation_from_an_earlier_attempt_does_not_count`: calculate, `fail_claim`, take again, approve; the result is 422.
  - `test_cents_noise_matches`: the calculation 419.999999 against the approval 420.00 succeeds.
  - `test_a_complete_approval_holds_its_calculation_maker_time_and_manual_version`: `decision.calculation.lines == [{"step": "Payout", "amount": 420.0}]`, `decided_by == "Test"`, `manual_version == "2026.2"`, and `decided_at` is ISO.
  - `test_a_decline_has_no_calculation`
  - `test_the_latest_calculation_counts`
- [ ] **Step 2: Run them.**
  - Run: `.venv\Scripts\python -m pytest tests/test_section14.py -q`
  - Expected: FAIL.
- [ ] **Step 3: Implement.** Move every existing backend test that records an approval onto `record_approval`/`write_calculation`, leaving its expected payouts alone. These include `test_api.py`, `test_payments.py`, `test_claims.py` and `test_api_key.py`.
- [ ] **Step 4: Run them again.**
  - Expected: PASS for `test_section14.py`; the remaining failures are only the agent's (Task 6).

### Task 6: The agent fills in the maker; refusals go back to the model

**Files:**
- Modify:
  - `backend/agent/tools.py`: `save_decision` becomes `record_decision(decision, payout, rationale, citations)`, which calls `route.app("record_decision", {claim_id, ..., decided_by, model})`. On success it is a `Command` that sets `proposal` and `decision`; on a refusal it is the error text.
  - `backend/agent/graph.py`:
    - `decide` no longer calls the app; it only adds warnings and the decision step.
    - `screen` records the referral with `decided_by="Security check"` and no calculation.
    - The graph takes `decided_by: str`.
  - `backend/agent/worker.py`: `decided_by = f"UnderCover Agent, configuration version {config['version']}"`.
  - `backend/agent/scripted.py`: calls `calculate_payout` before an approval.
  - `backend/agent/instructions_claim.md` (tool names), `e2e/tests/fixtures.ts` (`recordApproval(request, claimId, payout)` with the same recipe), and the e2e specs that record approvals.
- Test: `backend/tests/test_graph.py`, `backend/tests/test_screening.py`

**Interfaces:**
- Consumes: Task 5's route, messages and helpers.
- Produces: `build_claim_graph(..., route: Route, decided_by: str)`.

- [ ] **Step 1: Write the failing tests.**
  - `test_a_section_14_refusal_goes_back_to_the_model_which_tries_again`: the stub approves with no calculation, then calculates, then approves. The claim ends paid, and there is one `refusal` step with `by == "app"` whose reason starts "Claims manual section 14".
  - `test_the_record_says_the_agent_and_its_version_made_it`
  - `test_a_referral_by_the_check_says_security_check`: update `test_screening.py`'s first test to assert `decided_by == "Security check"`.
  - `test_the_model_never_sees_decided_by`: no bound tool's schema has `decided_by` or `claim_id`.
- [ ] **Step 2: Run them.**
  - Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the whole backend suite and the e2e.**
  - Expected: all pass.
- [ ] **Step 5: Commit the phase:** `git commit -m "feat: decision records meet claims manual section 14"`

---

## Phase 4: Memory of each customer (spec step 4)

### Task 7: AgentCore Memory, the `remember` node and `search_memory`

**Files:**
- Create: `backend/agent/memory.py`
- Modify:
  - `backend/pyproject.toml` and the lock: add `langgraph-checkpoint-aws`.
  - `backend/agent/graph.py`: a `remember` node after `decide`, with the edges `decide -> remember -> pay | END`.
  - `backend/agent/tools.py` (`search_memory`), `backend/agent/worker.py` (opens the store at build time; tells the graph whether the run is an evaluation), `backend/agent/events.py`.
  - `backend/agent/instructions_claim.md`: goals that name the context layer and the memory.
  - `backend/agent/improver_instructions.md`: the sentence listing the tools.
- Test: `backend/tests/test_memory.py`; `FakeMemoryStore` in `fakes.py`

**Interfaces:**
- Produces:
  - **Opening the store:** `memory.open_store() -> BaseStore | None`. It returns `AgentCoreMemoryStore(memory_id=os.environ["UNDERCOVER_MEMORY_ID"], region_name=boto3.Session().region_name)` when the variable is set, and `None` otherwise.
  - **Saving:** `memory.save_claim(store, claim: dict, decision: dict) -> dict`. It writes two events:
    - `store.put((customer_id, claim_id), "claim", {"message": HumanMessage(<json of summary, claim_type, incident_date, incident_location, items>)})`
    - then `store.put((customer_id, claim_id), "decision", {"message": AIMessage(<json of decision, payout, citations, rationale>)})`

    It returns what it saved. Never saved: the story and the documents.
  - **Searching:** `memory.search(store, customer_id: str, claim_id: str, query: str) -> list[str]`. It calls `store.search(("customers", customer_id), query=query, limit=5)`, drops any item whose namespace contains `claim_id`, and returns `item.value["content"]`.
  - **The steps:**
    - `memory_save`: `{"saved": [claim_event, decision_event]}`, or `{"error": str}` with a logged warning.
    - `memory_search`: `{"query", "found": [str]}`.
  - **The `remember` node:**
    - It skips when there is no store, when the claim is an evaluation's copy (`claim_state(...)["copy_of"]` is set), or when the decision was the security check's (that path never reaches `remember`, since `screen -> END`).
    - An error from AgentCore is logged; the step records the error; the decision stays.
  - **The `search_memory(query: str)` tool:** "Search what we remember about this claim's customer from their earlier claims." It is bound only when `store is not None and not evaluation`. An error becomes the model's refusal.
  - **Building the graph:** `AgentEnv.claim_graph(run_id, config)` computes `evaluation = run_id is not None and api.get_run(run_id)["kind"] == "evaluation"`.

- [ ] **Step 1: Write the failing tests** with `FakeMemoryStore`. It records `put` calls and answers `search` from fixed items carrying namespaces.
  - `test_after_a_decision_two_events_are_saved_customer_as_actor_claim_as_session`: the first is a `HumanMessage`, the second an `AIMessage`, and the JSON has no `story` or `documents`.
  - `test_nothing_is_saved_for_a_claim_the_check_referred`
  - `test_nothing_is_saved_for_an_evaluations_copy`
  - `test_a_restarted_claim_saves_once`: interrupt after `remember` (an approval held for sign-off), then `carry_on`; `put` count is 2.
  - `test_search_is_kept_to_the_claims_customer_and_leaves_out_this_claims_summary`
  - `test_no_memory_tool_when_memory_is_off_or_in_an_evaluation`: assert on the bound tool names.
  - `test_an_unreachable_memory_logs_and_keeps_the_decision`
  - `test_a_failed_search_is_a_refusal_the_model_sees`
  - `test_open_store_reads_the_environment_when_called`: patch `AgentCoreMemoryStore`.
- [ ] **Step 2: Run them.**
  - Run: `.venv\Scripts\python -m pytest tests/test_memory.py -q`
  - Expected: FAIL.
- [ ] **Step 3: Implement.** Run `uv add langgraph-checkpoint-aws`, then write the code and the instructions.
- [ ] **Step 4: Run the whole backend suite.**
  - Expected: all pass. With `npm run dev` and no `UNDERCOVER_MEMORY_ID`, `uv run -m agent CLM-1002` works with no `search_memory` among the `tool_call`s.
- [ ] **Step 5: Commit the phase:** `git commit -m "feat: the agent remembers each customer in AgentCore Memory"`

---

## Phase 5: Every step on the claim page (spec step 5)

### Task 8: The steps, Context used and the whole decision record

**Files:**
- Modify:
  - `backend/agent/events.py`: on the gateway route, `tool_call`, `tool_result` and `lookup` carry the shown name from `route.shown_name`.
  - `backend/app/api.py`: `GET /api/claims/{id}` replaces `read` with `context_used: [{"file", "system", "owner", "reviewed", "lookups": [{"tool", "args"}]}]`, grouped by file. Each lookup is listed once (by tool plus args), in the order first used. `_SECTION_READ` and `read_sections` are deleted.
  - `frontend/src/api.ts`: the step kinds `lookup | memory_search | memory_save | refusal`, plus `ContextUsed` and the decision fields.
  - `frontend/src/pages/Trace.tsx`: a row for each new kind, with refusals styled as `trace-refusal` and labelled "Refused by the Gateway" / "Refused by the app" / "Refused by the context layer".
  - `frontend/src/pages/ClaimDetail.tsx`: a "Context used" panel in place of "What the agent read". The decision panel shows the decision, the payout, the calculation line by line, the sections relied on, the reason, who decided, when, the manual's version, and, if signed off, who and when.
  - `frontend/src/styles/*`, following `docs/BRAND.md`.
- Test: `backend/tests/test_api.py`, `frontend/src/pages/Trace.test.tsx`, `frontend/src/pages/ClaimDetail.test.tsx`

**Interfaces:**
- Consumes: the step shapes from Tasks 2, 4 and 7, and the decision view from Task 5.

- [ ] **Step 1: Write the failing tests.**
  - `test_context_used_groups_lookups_by_file_once_each`, which also checks that a refusal step adds nothing.
  - `test_tool_calls_on_the_gateway_route_carry_the_gateways_names`
  - Trace tests, one per kind: `lookup` shows what was looked up and "glossary.yaml · The policy wording, version 2026.1 · Legal · 2026-09-01"; `memory_search` shows the query and what it found; `memory_save` shows what was saved; `refusal` has class `trace-refusal` and its "Refused by …" label.
  - ClaimDetail tests: "Context used" lists the files and their sources; the decision panel shows each calculation line and the total, "UnderCover Agent, configuration version 1", and "Claims manual 2026.2".
- [ ] **Step 2: Run them.**
  - Run: `pytest tests/test_api.py -q`; `npx vitest run src/pages`
  - Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full check from a clean worktree.**
  - Run: `git worktree add ../context-check HEAD`, then `npm run check` there, in parts if memory runs short (backend, the two import checks, vitest, tsc, lint, e2e).
  - Expected: all green with no `.env`.
- [ ] **Step 5: Commit the phase:** `git commit -m "feat: every step of the agent's work on the claim page"`

---

## Docs and live checks

### Task 9: Docs, then the live runs

**Files:**
- Modify:
  - `docs/SPEC.md`: the context layer, the memory and the complete record.
  - `docs/TECH_STACK.md`, Deployment section:
    - The context package is `backend/context/` zipped as `context/`, without tests, with the `context` group for Linux and py3.12; its handler is `context.server.handler`; it sits behind a Function URL with `AWS_IAM` auth and has no env vars.
    - The app's package leaves out `backend/context/`.
    - It lists the agent's three settings plus the AWS CLI credentials.
  - `docs/PROCESS.md`: four parts in `npm run dev`, with the context layer on 8006.
  - `docs/CODE_STANDARDS.md`: how the agent reaches the backend, with one module per route (`direct.py`, `gateway.py`) and bookkeeping always direct.
  - `docs/STATUS.md` and `data/README.md` (`plans.json` and the two `.md` files are kept but no longer read).
  - Move this plan to `docs/plans/` when it is ticked off.

- [ ] **Step 1: Write the docs.** Commit them: `git commit -m "docs: the context layer, memory and the complete record"`.
- [ ] **Step 2: Reseed once.** Run `npm run seed`. Start `npm run dev` on the default model, on the direct route, with memory off (no `UNDERCOVER_GATEWAY_URL` or `UNDERCOVER_MEMORY_ID`).
- [ ] **Step 3: Work the two claims.** Run `cd backend; uv run -m agent CLM-1005`, then `uv run -m agent CLM-1006`.
  - Expected: each prints a decision, and its record has a calculation if it is an approval, plus `decided_by` and `manual_version`.
- [ ] **Step 4: Run a dispatch at N=25** from the dashboard (with `e2e/live_security.mjs`'s login pattern) on the rest of the queue.
  - Expected: it works as before; CLM-1901 is referred by the security check.
- [ ] **Step 5: Evaluate the evaluation set's five tiny claims.**
  - Expected: it finishes and shows its scores.
- [ ] **Step 6: Check the claim pages as Dana Whitfield through the Playwright MCP server.** Open CLM-1005 and CLM-1006 and check that:
  - each tool call is shown;
  - each lookup says where it came from;
  - the Context used panel lists the files;
  - the decision shows its calculation.

  Sign in through a script so the password never passes through a tool call (as `e2e/live_security.mjs`), or ask the owner to sign in.
- [ ] **Step 7: Report what the runs spent,** from `model_calls`, in `docs/STATUS.md`. Commit it.
