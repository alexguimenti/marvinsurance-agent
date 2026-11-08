# MarvInsurance: Marvin Agent

A course project (nothing here is real). The Marvin Agent works a travel-insurance claims queue like a human analyst. A dispatcher, which is code and no model, hands every claim to a claim agent of its own, up to 25 at once, and sums up the run from the records. The claim agent reads the claim, the policy and the customer's history, looks up the sections of the policy wording and the claims manual it needs, then approves, declines or refers. Payouts above $1,000 wait for a handler.

See `docs/SPEC.md` for what we build, `docs/TECH_STACK.md` for the stack, `docs/STATUS.md` for where it stands, `docs/plans/HARNESS.md` for the harness requirements, and `docs/plans/` for the finished implementation plans.

## Status

- **Pilot** (`docs/plans/PLAN.md`): done. Queue, claim detail, live activity and approvals; the $1,000 limit enforced by the payment code.
- **Harness** (`docs/plans/PLAN_HARNESS.md`): done. One run works the whole queue, or a capped part of it, through a to-do list shown on the queue page. Each claim gets a fresh claim agent. The agent looks up sections of the policy wording and the claims manual, and the claim page shows what it read. A claim that fails goes back in the queue; one that fails twice is left for a handler ("Needs a handler"). Default model: DeepSeek V4.1 Flash, low reasoning effort.
- **Durable agent** (`docs/PLAN_DURABLE.md`): done. See `docs/STATUS.md`. The claim agent waits for a sign-off and then pays, carries on after a restart, and never decides or pays twice. Records on Supabase, traces in Langfuse.
- **Swarm and scoreboard** (`docs/PLAN_SWARM.md`): done. A dispatch works the queue with N claim agents at once; evaluations on copies of the labelled sets show on the Scoreboard with the labs' measures; every claim's cost and time are recorded with its run.
- **Improver and sign-in** (`docs/PLAN_IMPROVE.md`): done. Handlers sign in with Supabase Auth and may do what their rights allow; the approval limit and the improver's limits are settings; the claim agent's configuration is kept in versions; a judge grades every explanation; an improver agent proposes changes in rounds, a handler approves them and code keeps or rolls back each version on the held-out claims.
- Not built: real payments, messages to customers, rejecting a held payout.

## Setup

```bash
# backend (Python 3.11+)
cd backend && uv sync --extra dev

# frontend and e2e
cd ../frontend && npm install
cd ../e2e && npm install && npx playwright install chromium
```

Create a `.env` in the repo root (git-ignored):

```
OPENROUTER_API_KEY=...
DATABASE_URL=...            # Supabase session pooler; leave out to use a local SQLite file
VITE_SUPABASE_URL=...       # sign-in: the same values as SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY
VITE_SUPABASE_PUBLISHABLE_KEY=...
HANDLER_PASSWORD=...        # the demo handlers' password, for the live checks
LANGFUSE_PUBLIC_KEY=...     # all three optional: no keys, no tracing
LANGFUSE_SECRET_KEY=...
LANGFUSE_BASE_URL=...
```

The model and how it thinks are set in **`agent.toml`** at the repo root (committed, no secrets):

```toml
[agent]
model = "deepseek/deepseek-v4.1-flash"   # any OpenRouter model id
temperature = 0
max_attempts = 2                         # failed attempts before a claim is left for a handler
# step_retries = 2                       # retries of one model call (timeout, provider error)
# claim_timeout_s = 300                  # time limit for one attempt at one claim
# max_model_calls = 30                   # model calls for one claim
```

The default model runs with low reasoning effort. `MODEL_NAME` in the environment chooses another model, which runs with no reasoning effort set. `AGENT_TEMPERATURE` overrides the temperature. `AGENT_PROVIDER=scripted` swaps the model for a deterministic fake (used by the tests). `DB_PATH` and `DATA_DIR` are optional.

## Run

All the commands you need every day (start, auto-reload, tests, reset) are in **[COMMANDS.md](COMMANDS.md)**.

```bash
npm run dev      # the backend, the agent's service and the dashboard: http://localhost:5173
```

Sign in as a handler first. Screens: **Claims** (the queue, N and **Start dispatch**, the run panel with the dispatch's claims, cost, time and summary, and claim detail with the agent's live activity and what it read), **Approvals** (held payouts), **Scoreboard** (evaluations, their measures and the judge's grade), **Improvement** (rounds, proposals to approve, scores by version), **Configuration** (the claim agent's versions and the improver's recipe) and **Settings** (the limits, and light or dark).

To try the agent cheaply, start an evaluation of five tiny claims on the Scoreboard: it works copies and leaves the queue as it was.

## Test

```bash
npm run check                                          # everything below, in order, from the repo root
cd backend && ./.venv/Scripts/python -m pytest        # unit tests (the real-model smoke test runs only with RUN_LIVE_SMOKE=1)
cd frontend && npm test -- --run                       # component tests
cd e2e && npm test                                     # end-to-end, scripted agent, throwaway database
```

No test calls a model. `npm run check` needs no `.env`. A scripted model stands in, and fails on purpose on two claims so recovery can be tested: CLM-1013 always fails and is left for a handler, and CLM-1017 fails once and is then decided.

## Reseed and test again

To forget every analysis and evaluation and put all 25 claims back in the queue, on the database in `.env`. Do this once before live runs with the real model:

```powershell
npm run seed
```

## Score the agent

On the Scoreboard. Seeding is the only code that reads `data/evals/`, into tables the agent never reads; the backend scores each evaluation from them.

### Earlier results (queue, 25 claims, 2026-10-29, with the old `scripts/score.py`)

| Model | Decision accuracy | Payout accuracy |
|---|---|---|
| `openai/gpt-4o-mini` | 52% | 28% |
| `openai/gpt-4o` | 60% | 52% |

Both models approve claims the handlers declined or referred. Of the 6 expected referrals, 3 need the claims manual or the customer history, which the pilot left out on purpose, so some misses were expected. The harness now gives the agent the manual; it has not been scored yet. The held-out set has not been scored yet.

## Layout

```
backend/   records and API (app/), the agent and its service (agent/), tests (tests/)
frontend/  React (Vite, TypeScript), Vitest
e2e/       Playwright
scripts/   dev.mjs, check.mjs, reseed.mjs, reset_db.py, check_live.py
data/      seed data; data/evals is read only by seeding, data/security not at all
docs/      SPEC, TECH_STACK, DESIGN, PROCESS, CODE_STANDARDS, STATUS, the swarm and improver briefs and plans; plans/ holds finished plans
labs/, lab1_desk/, lab2_desk/   course notebooks and lab desks (each desk has its own .venv); not part of the app
```
