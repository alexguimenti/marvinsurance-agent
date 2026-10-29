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
- SQLite stores the app's state: claims and their status, agent decisions and rationales, the step-by-step activity trace, and payments with their approval state. A single local file is enough for a pilot with no sign-in and no real payments.
- The seed files in `data/` (claims, policies, customers, plans, handlers, company settings) are loaded into SQLite at startup or by a seed script.
- The $1,000 payout limit is enforced in the backend payment code, not in the agent. A payout over the limit stays blocked until a handler approves it (see `SPEC.md`, section 4).
- `data/evals/` and `data/security/` are never loaded into the app database or exposed through the API.

## Agents: LangChain and OpenRouter

- LangChain builds the Farting Agent: its prompt, its tools (read claim, policy, customer, plan, policy wording; calculate payout; record decision; request payment) and the loop that runs them.
- OpenRouter is the single gateway to models, called through LangChain's OpenAI-compatible chat model with the OpenRouter base URL.
- **The model is configurable.** The model ID is a setting, never hardcoded in the agent code. Switching models, or comparing two on the same claims, means changing configuration only.

### Configuration

Environment variables, kept in a local `.env` file that is not committed:

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter credential |
| `AGENT_MODEL` | OpenRouter model ID used by the agent (for example `provider/model-name`) |
| `AGENT_TEMPERATURE` | Optional sampling temperature, default `0` for consistent decisions |

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

## Open items

- Server-sent events instead of polling, if the activity view needs to feel more live.
