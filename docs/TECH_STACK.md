# Tech Stack

Technology choices for the Farting Agent pilot. See `SPEC.md` for what we are building and `DESIGN.md` for the visual style.

## Summary

| Layer | Choice |
|---|---|
| Backend | FastAPI (Python) |
| Database | SQLite |
| Agent framework | LangChain |
| Model access | OpenRouter |
| Frontend | React |

## Backend: FastAPI and SQLite

- FastAPI serves the API used by the React screens: the claims queue, claim detail, agent activity, agent instructions and payment approvals.
- SQLite stores the app's state: claims and their status and failed attempts, agent decisions and rationales, the step-by-step activity trace, payments with their approval state, and runs with their to-do list and events. A database from an earlier version is upgraded in place at startup. A single local file is enough for a pilot with no sign-in and no real payments.
- The seed files in `data/` (claims, policies, customers, plans, handlers, company settings) are loaded into SQLite at startup or by a seed script.
- The $1,000 payout limit is enforced in the backend payment code, not in the agent. A payout over the limit stays blocked until a handler approves it (see `SPEC.md`, section 4).
- `data/evals/` and `data/security/` are never loaded into the app database or exposed through the API.

## Agents: LangChain and OpenRouter

- LangChain builds the Farting Agent in two roles, both with `create_agent` and no other agent framework:
  - The **orchestrator** runs the queue. Its tools list the waiting claims, write and update the to-do list, hand a claim to a claim agent and finish the run. Its instructions are `backend/app/instructions_run.md`.
  - The **claim agent** works one claim from a clean start, so each claim's work stays focused and the run does not get more expensive as it goes. It starts with the claim, policy, plan and customer history already in its conversation (as results of tool calls it did not have to make) and the section index of both documents in its prompt. Its tools read and search sections of the policy wording and the claims manual (at most 8 per claim, no rereads), look up public facts, calculate the payout and request payment. It returns a structured `Decision`. Its instructions are `backend/app/instructions_claim.md`.
- Our code, not the model, enforces the run's cap and scope, the attempt rules, the $1,000 limit and the missed-claim check (`backend/app/runner.py`, `runs.py`, `orchestrator.py`).
- OpenRouter is the single gateway to models, called through LangChain's OpenAI-compatible chat model with the OpenRouter base URL.
- **The model is configurable.** The model ID is a setting, never hardcoded in the agent code. Switching models, or comparing two on the same claims, means changing configuration only. The default is DeepSeek V4.1 Flash (`deepseek/deepseek-v4.1-flash`) with low reasoning effort, set in `agent.toml`; both roles use the same model.

### Configuration

Environment variables, kept in a local `.env` file that is not committed:

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter credential |
| `AGENT_MODEL` | OpenRouter model ID used by the agent (for example `provider/model-name`) |
| `AGENT_TEMPERATURE` | Optional sampling temperature, default `0` for consistent decisions |

The same settings, and the harness limits (`max_attempts`, `step_retries`, `claim_timeout_s`, `max_steps`, `run_max_steps`), live in `agent.toml` at the repo root. Environment variables win over it.

Each recorded decision stores the model ID that produced it, so results can be compared across models.

## Frontend: React

- React single-page app for the screens listed in `SPEC.md`, section 6.
- It talks to the FastAPI backend over HTTP and follows `DESIGN.md` for tokens, type and components.
- Agent activity is shown live while a claim is in progress. The transport (polling or server-sent events) is still open.

## Testing

Everything we implement must come with **unit tests and end-to-end (e2e) tests**. A feature is not done until both pass.

- **Unit tests** cover the logic in isolation: backend functions and API routes, the payout calculation, the $1,000 approval-limit block, status transitions, the agent's tools and the React components. Unit tests never call a real model; the model is replaced with a fake.
- **E2E tests** cover whole flows through the running app, from the React screens through the API to SQLite. For example: a claim moves from waiting to decided, and a payout over $1,000 stays blocked until a handler approves it.
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
