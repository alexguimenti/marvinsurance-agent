# Tech Stack

Technology choices for the Marvin Agent pilot. See `SPEC.md` for what we are building and `DESIGN.md` for the visual style.

## Summary

| Layer | Choice |
|---|---|
| Backend | FastAPI (Python) |
| Database | Supabase Postgres when `DATABASE_URL` is set; SQLite for tests and local runs. Through SQLAlchemy Core |
| Agent framework | LangGraph (claim agent), LangChain `create_agent` (queue agent) |
| Tracing | Langfuse |
| Model access | OpenRouter |
| Frontend | React |

## Backend: FastAPI and the database

- FastAPI serves the API used by the React screens: the claims queue, claim detail, agent activity, agent instructions and payment approvals.
- The database holds the app's state: claims and their status and failed attempts, agent decisions and rationales, the step-by-step activity trace, payments with who signed off and when, and runs with their to-do list and events.
- **Supabase** is used only when `DATABASE_URL`, the session pooler connection string, is in the process environment. `npm run dev` and `npm run reseed` pass `.env`; the tests never do, and run on throwaway SQLite files. A `DATABASE_URL` in `.env` alone does nothing.
- **SQLAlchemy Core** means each query is written once for both. All our tables, the checkpointer's included, are in the schema `undercover`, set on each Postgres connection, with row level security on.
- **Round trips:** every statement commits by itself and connections are opened at startup, so a query is one trip to Supabase and a sign-off can show as paid within five seconds (see `CODE_STANDARDS.md`).
- **Connections:** the free plan allows 15 at once through the session pooler, across every process, so each process uses at most 4: the backend 4, the agent 2 for records and 2 for checkpoints.
- `npm run reseed` makes our tables again, clears the checkpoints and loads the claims, on whichever database is in `.env`.
- The seed claims are loaded at startup when missing, and by the reseed. Policies, customers, plans, handlers and company settings are read from `data/`.
- The payout rules are in the backend (`app/payout.py`, `POST /payouts/calculate`, and the agent's `calculate_payout` tool), with the approval limit from `data/company.json`.
- The $1,000 payout limit is enforced in the backend payment code, not in the agent. A payout over the limit stays blocked until a handler approves it (see `SPEC.md`, section 4). A sign-off only records who and when: the agent pays.
- **API rules:** every operation has an `operation_id`, a one-line docstring and no optional inputs. A test checks this for the backend and the service.
- `data/evals/` and `data/security/` are never loaded into the app database or exposed through the API.

## Agents: LangGraph, LangChain and OpenRouter

- The Marvin Agent has two roles, in `backend/agent/`, and runs as a small web service of its own on port 8002 (`UNDERCOVER_AGENT` in the environment, or `http://127.0.0.1:8002`). The backend calls it to start runs and after each sign-off, and doesn't wait for the payment. `uv run -m agent` also starts runs. See `CODE_STANDARDS.md` for its parts.
  - The **queue agent** runs the queue, with LangChain's `create_agent`. Its tools list the waiting claims, write and update the to-do list, hand a claim to a claim agent and finish the run. Its instructions are `backend/agent/instructions_run.md`.
  - The **claim agent** is a hand-written LangGraph graph (`backend/agent/graph.py`). It works one claim from a clean start, so each claim's work stays focused and the run does not get more expensive as it goes. It starts with the claim, policy, plan and customer history already in its conversation (as results of tool calls it did not have to make) and the section index of both documents in its prompt. Its tools read and search sections of the policy wording and the claims manual (at most 8 per claim, no rereads), look up public facts, calculate the payout and save its decision. Paying is a step of the graph, not a tool. It waits for a sign-off with LangGraph's `interrupt()` when the backend says a claim needs one. Every step is recorded on the claim, refusals go back to the model, and it makes at most 30 model calls. Its instructions are `backend/agent/instructions_claim.md`.
- Our code, not the model, enforces the run's cap and scope, the attempt rules, the $1,000 limit and the missed-claim check (`backend/app/claims.py`, `payments.py`, `runs.py`; `backend/agent/worker.py`, `queue.py`).
- OpenRouter is the single gateway to models, called through LangChain's OpenAI-compatible chat model with the OpenRouter base URL.
- **The model is configurable.** The model ID is a setting, never hardcoded in the agent code. Switching models, or comparing two on the same claims, means changing configuration only. The default is DeepSeek V4.1 Flash (`deepseek/deepseek-v4.1-flash`) with low reasoning effort, set in `agent.toml`; both roles use the same model.

- **Progress** is saved after every step with LangGraph's checkpointer, with `durability="sync"`: `PostgresSaver` in the same Supabase database, or `SqliteSaver` in a file beside the local database. The agent is synchronous, because async Postgres connections don't work with Windows' default event loop. Each attempt at a claim has its own thread, `CLM-1003-1`.
- **Langfuse** traces every claim when its keys are in the environment. Each thread is one session, named after the claim and its thread (`CLM-1003:CLM-1003-1`), so work before and after a pause is together. A tag with the claim id finds every attempt. Tracing never fails a claim.

### Configuration

Environment variables, kept in a local `.env` file that is not committed:

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter credential |
| `AGENT_MODEL` | OpenRouter model ID used by the agent (for example `provider/model-name`) |
| `AGENT_TEMPERATURE` | Optional sampling temperature, default `0` for consistent decisions |
| `DATABASE_URL` | Supabase session pooler connection string. Only from the process environment |
| `UNDERCOVER_AGENT` | Address of the agent's service. Only from the process environment |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` | Langfuse. Only from the process environment |

The same settings, and the harness limits (`max_attempts`, `step_retries`, `claim_timeout_s`, `max_model_calls`, `run_max_steps`), live in `agent.toml` at the repo root. Environment variables win over it.

Each recorded decision stores the model ID that produced it, so results can be compared across models.

## Frontend: React

- React single-page app for the screens listed in `SPEC.md`, section 6.
- It talks to the FastAPI backend over HTTP and follows `DESIGN.md` for tokens, type and components.
- Agent activity is shown live while a claim is in progress. The transport (polling or server-sent events) is still open.

## Testing

Everything we implement must come with **unit tests and end-to-end (e2e) tests**. A feature is not done until both pass.

- **Unit tests** cover the logic in isolation: backend functions and API routes, the payout calculation, the $1,000 approval-limit block, status transitions, the agent's tools and the React components. Unit tests never call a real model; the model is replaced with a fake.
- **E2E tests** cover whole flows through the running app, from the React screens through the API to SQLite, on the scripted model. A stand-in for the agent's service on port 8003 checks that a sign-off on the dashboard reaches it; the Python tests cover the resume and the payment.
- Tests run against a throwaway SQLite database, never the working one.
- Tests must not read `data/evals/`. Scoring the agent against the handlers' decisions is a separate evaluation, not part of the test suite.
- New code and bug fixes ship with their tests in the same change.

## Decisions made in the pilot

- Python 3.11+ (built on 3.13), dependencies in `backend/pyproject.toml`.
- Frontend: Vite, TypeScript, React Router. The activity view polls the API every second while a claim is in progress.
- Tests: pytest (backend), Vitest with Testing Library (frontend), Playwright (end-to-end, scripted agent and a throwaway database).
- The agent is a LangChain `create_agent` with a structured `Decision` output, calling OpenRouter through `ChatOpenAI` with the OpenRouter base URL.
- The harness adds an orchestrator on the same model, a fresh claim agent per claim, and runs and to-do items in SQLite. The queue page polls the latest run every 2 seconds.
- `npm run check` at the repo root runs every suite: pytest, Vitest, the TypeScript check, oxlint (no warnings allowed) and Playwright. The live OpenRouter smoke test runs only with `RUN_LIVE_SMOKE=1`.

## Open items

- Server-sent events instead of polling, if the activity view needs to feel more live.
