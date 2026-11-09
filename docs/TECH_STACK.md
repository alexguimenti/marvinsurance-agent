# Tech Stack

Technology choices for the Marvin Agent pilot. See `SPEC.md` for what we are building and `DESIGN.md` for the visual style.

## Summary

| Layer | Choice |
|---|---|
| Backend | FastAPI (Python) |
| Database | Supabase Postgres when `DATABASE_URL` is set; SQLite for tests and local runs. Through SQLAlchemy Core |
| Agent framework | LangGraph: the claim agent's graph, and the dispatcher's graph with no model |
| Tracing | Langfuse |
| Model access | OpenRouter |
| Frontend | React |
| Sign-in | Supabase Auth: the dashboard signs in, the backend checks the token with PyJWT |
| Judge | Jev (`typesafe/jev-1.13`) through the `openrouter` package |

## Backend: FastAPI and the database

- FastAPI serves the API used by the React screens: the claims queue, claim detail, agent activity, payment approvals, the settings, the configuration versions and the improvement rounds.
- The database holds the app's state: claims and their status and failed attempts, agent decisions and rationales, the step-by-step activity trace, payments with who signed off and when, dispatches and evaluations with their claims, each model call's tokens and cost, each stretch of agent time, and the answer key for evaluations.
- **Supabase** is used only when `DATABASE_URL`, the session pooler connection string, is in the process environment. `npm run dev` and `npm run reseed` pass `.env`; the tests never do, and run on throwaway SQLite files. A `DATABASE_URL` in `.env` alone does nothing.
- **SQLAlchemy Core** means each query is written once for both. All our tables, the checkpointer's included, are in the schema `undercover`, set on each Postgres connection, with row level security on.
- **Round trips:** every statement commits by itself and connections are opened at startup, so a query is one trip to Supabase and a sign-off can show as paid within five seconds (see `CODE_STANDARDS.md`).
- **Connections:** the free plan allows 15 at once through the session pooler, across every process, so each process uses at most 4: the backend 4, the agent 2 for records and 2 for checkpoints. Up to 25 claim agents in the agent's process share its 4 and wait for one (up to 600 s) rather than fail.
- `npm run seed` (or `npm run reseed`) makes our tables again, clears the checkpoints and loads the claims and the answer key, on whichever database is in `.env`. It empties the scoreboard too, and drops the old to-do tables.
- The seed claims are loaded at startup when missing, and by the reseed. Policies, customers, plans, handlers and company settings are read from `data/`.
- The payout rules are in the backend (`app/payout.py`, `POST /payouts/calculate`, and the agent's `calculate_payout` tool), with the approval limit from `data/company.json`.
- The $1,000 payout limit is enforced in the backend payment code, not in the agent. A payout over the limit stays blocked until a handler approves it (see `SPEC.md`, section 4). A sign-off only records who and when: the agent pays.
- **Sign-in:** the dashboard signs a handler in with `signInWithPassword` (`@supabase/supabase-js`) and sends the access token as `Authorization: Bearer` on every request. The backend (`app/auth.py`) checks the token with PyJWT against the project's JWKS (`<VITE_SUPABASE_URL>/auth/v1/.well-known/jwks.json`; ES256 or RS256, audience `authenticated`), then finds the handler by email in `data/handlers.json`. `signed_in` and `senior` are FastAPI dependencies; actions need a token, reads stay open. A 401 signs the handler out.
- **Routes added by this change:** `GET /me`; `GET /settings`, `PUT /settings/{name}` (senior); `GET /configuration`, `GET /configuration/{kind}/{version}/diff`; `POST /rounds`, `POST /rounds/{id}/stop`, `POST /rounds/{id}/decision`, `GET /rounds`, `GET /rounds/{id}`. `/handlers` and `/instructions` are gone: the handler comes from the token, and the instructions are a version.
- **Settings and versions:** tables `settings` and `settings_history` (name, JSON value, who, when), `config_versions`, `recipe_versions` and `current_version`, `judge_grades`, `rounds` (one open at a time, by a unique index) and `proposals`. A run records `config_version` and `round_id`, a model call its `round_id`, a decision the `approval_limit` it was made under, and the answer key the handler's `reference` reason. `configs.config_for_run` is one join, so a claim agent reads its version in one trip. A handler's decision on a proposal is one conditional `UPDATE`, because each statement commits by itself.
- **API rules:** every operation has an `operation_id`, a one-line docstring and no optional inputs. A test checks this for the backend and the service.
- Seeding loads `data/evals/` into tables of their own: `held_out_claims`, `eval_sets` (which claim is in which set, and whether it is tiny) and `answer_key` (the handlers' decisions). Only `app/scoring.py` reads `answer_key`; the agent never reads any of them. `data/security/` is never loaded.

## Agents: LangGraph, LangChain and OpenRouter

- The Marvin Agent is in `backend/agent/` and runs as a small web service of its own on port 8002 (`UNDERCOVER_AGENT` in the environment, or `http://127.0.0.1:8002`). The backend calls it to start a dispatch or an evaluation, passing N and the set, and after each sign-off; the service answers at once and works in the background. When it starts, it marks any run still running as stopped. `uv run -m agent CLM-1001` works one claim. See `CODE_STANDARDS.md` for its parts.
  - The **dispatcher** (`backend/agent/dispatcher.py`) is a LangGraph graph with no model, in the style of `labs/week2day4_mex.ipynb`: `list_claims` fans out one `Send` per claim to `work_claim`, with `max_concurrency` set to N. `work_claim` calls `worker.work`, the one function that touches the claim agent, and has a `RetryPolicy` on LangChain's `ModelRateLimitError` and `ModelTimeoutError` and an `error_handler` that leaves a claim in progress for the next dispatch. It saves no progress of its own.
  - The **claim agent** is a hand-written LangGraph graph (`backend/agent/graph.py`). It works one claim from a clean start, so each claim's work stays focused and the run does not get more expensive as it goes. It starts with the claim, policy, plan and customer history already in its conversation (as results of tool calls it did not have to make) and the section index of both documents in its prompt. Its tools read and search sections of the policy wording and the claims manual (at most 8 per claim, no rereads), look up public facts, calculate the payout and save its decision. Paying is a step of the graph, not a tool. It waits for a sign-off with LangGraph's `interrupt()` when the backend says a claim needs one. Every step is recorded on the claim, refusals go back to the model, and it makes at most 30 model calls. Its instructions are `backend/agent/instructions_claim.md`.
- **The judge** (`backend/app/judge.py`) calls Jev through the `openrouter` package with three questions: `grade`, `same_grounds` and `same_section`. It grades an evaluation's claims in parallel, with a call timeout of 10 s and a deadline of 15 s for the whole evaluation, before the evaluation is marked finished, and stores the result in `judge_grades`. It makes no call with no OpenRouter key, or when `MODEL_NAME` chooses another model for the run.
- **The improver** (`backend/agent/improver.py`) is a LangChain `create_agent` agent with four tools: `read_results`, `propose_change`, `run_evaluation` and `propose_recipe`. It runs on the default model at low effort, like the claim agent, with its instructions in `improver_instructions.md` wrapped around the current recipe. `improver_backend.py` is the one agent module that imports the records, the scoring and the judge, and a test on imports keeps the claim agent apart from them. It waits for a handler with `HumanInTheLoopMiddleware` and the same checkpointer as the claim agent, on the thread `round-<id>-<hex>`; its cost goes in `model_calls` with the round's id. Its evaluations run in a context of their own so their traces are not part of the improver's. Langfuse groups a round's traces in the session `round-<id>`.
- Our code, not the model, chooses a run's claims and keeps track of it, and enforces the attempt rules, the $1,000 limit and one decision and one payment per claim (`backend/app/claims.py`, `payments.py`, `runs.py`; `backend/agent/worker.py`, `dispatcher.py`).
- OpenRouter is the single gateway to models, called through LangChain's OpenAI-compatible chat model with the OpenRouter base URL.
- **The model is configurable.** The model ID is a setting, never hardcoded in the agent code. Switching models, or comparing two on the same claims, means changing configuration only. The default is DeepSeek V4.1 Flash (`deepseek/deepseek-v4.1-flash`) with low reasoning effort, set in `agent.toml`. `MODEL_NAME` in the environment chooses another model, which runs with no reasoning effort set; a run records its model and its effort, `none` when none was sent.

- **Progress** is saved after every step with LangGraph's checkpointer, with `durability="sync"`: `PostgresSaver` in the same Supabase database, or `SqliteSaver` in a file beside the local database. The agent is synchronous, because async Postgres connections don't work with Windows' default event loop. Each attempt at a claim has its own thread, `CLM-1003-1`, and each claim run gets a saver of its own, over the process's one pool: a saver holds a lock around every read and write, so one saver for 25 claim agents would make them take turns.
- **Cost and time:** the claim graph's model node records each reply's tokens and OpenRouter's `usage.cost` through `app.usage.record_model_call`; the worker records each graph run's time. Both carry the run's id, or none.
- **Langfuse** traces every claim when its keys are in the environment. Each thread is one session, named after the claim and its thread (`CLM-1003:CLM-1003-1`), so work before and after a pause is together. A tag with the claim id finds every attempt, and a tag `run-<id>` finds every claim of a dispatch or evaluation. Tracing never fails a claim.

### Configuration

Environment variables, kept in a local `.env` file that is not committed:

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter credential |
| `MODEL_NAME` | Another OpenRouter model for the agent, run with no reasoning effort. Only from the process environment |
| `AGENT_TEMPERATURE` | Optional sampling temperature, default `0` for consistent decisions |
| `DATABASE_URL` | Supabase session pooler connection string. Only from the process environment |
| `UNDERCOVER_AGENT` | Address of the agent's service. Only from the process environment |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` | Langfuse. Only from the process environment |
| `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY` | Supabase Auth, for the dashboard (both) and the backend (the URL, for the keys). The same values as `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`. The backend takes the URL only from the process environment |
| `HANDLER_PASSWORD` | The password the demo handlers' accounts were made with. Used by the live checks, never by the app |
| `OPENROUTER_DECISIONS_URL` | Where the judge calls, `https://openrouter.ai` by default. Tests and the end-to-end run point it at a stand-in |

The models the improver may propose, with their allowed reasoning efforts and prices, are in `data/models.json`; the objectives, the noise and tolerance, the round's spending limit and N start from `data/improvement.json` and are settings after that. The model, the temperature and the limits (`max_attempts`, `step_retries`, `claim_timeout_s`, `max_model_calls`, `max_tokens`) live in `agent.toml` at the repo root. Environment variables win over it.

Each recorded decision stores the model ID that produced it, so results can be compared across models.

## Frontend: React

- React single-page app for the screens listed in `SPEC.md`, section 6.
- It talks to the FastAPI backend over HTTP and follows `DESIGN.md` for tokens, type and components.
- Agent activity is shown live while a claim is in progress. The transport (polling or server-sent events) is still open.

## Testing

Everything we implement must come with **unit tests and end-to-end (e2e) tests**. A feature is not done until both pass.

- **Unit tests** cover the logic in isolation: backend functions and API routes, the payout calculation, the $1,000 approval-limit block, status transitions, the agent's tools and the React components. Unit tests never call a real model; the model is replaced with a fake.
- **E2E tests** cover whole flows through the running app, signed in as a handler, from the React screens through the API to SQLite, on the scripted model. A stand-in for Supabase Auth on port 8004 (it signs the tokens and serves their keys), one for the judge on 8005 and one for the agent's service on port 8003 writes down every start, with its N and set, and every sign-off that reaches it; it runs dispatches and evaluations on the real service. The Python tests cover the resume and the payment.
- Tests run against a throwaway SQLite database, never the working one.
- The tests seed the answer key like the app does, and check scoring against numbers worked out by hand. No test calls a model, so no test spends on an evaluation.
- New code and bug fixes ship with their tests in the same change.

## Decisions made in the pilot

- Python 3.11+ (built on 3.13), dependencies in `backend/pyproject.toml`.
- Frontend: Vite, TypeScript, React Router. The activity view polls the API every second while a claim is in progress.
- Tests: pytest (backend), Vitest with Testing Library (frontend), Playwright (end-to-end, scripted agent and a throwaway database).
- The claim agent calls OpenRouter through `ChatOpenAI` with the OpenRouter base URL.
- The queue page polls the latest dispatch every 2 seconds, and the Scoreboard its evaluations every 3.
- `npm run check` at the repo root runs every suite: pytest, Vitest, the TypeScript check, oxlint (no warnings allowed) and Playwright. The live OpenRouter smoke test runs only with `RUN_LIVE_SMOKE=1`.

## Open items

- Server-sent events instead of polling, if the activity view needs to feel more live.
