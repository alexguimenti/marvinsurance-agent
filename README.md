# Marvin FartInsurance: Farting Agent

A course project (nothing here is real). The Farting Agent works a travel-insurance claims queue like a human analyst. One run plans the queue as a to-do list, hands each claim to a fresh claim agent, and finishes with a summary for the handlers. The claim agent reads the claim, the policy and the customer's history, looks up the sections of the policy wording and the claims manual it needs, then approves, declines or refers. Payouts above $1,000 wait for a handler.

See `docs/SPEC.md` for what we build, `docs/TECH_STACK.md` for the stack, `docs/plans/HARNESS.md` for the harness requirements, and `docs/plans/` for the finished implementation plans.

## Status

- **Pilot** (`docs/plans/PLAN.md`): done. Queue, claim detail, live activity, instructions and approvals; the $1,000 limit enforced by the payment code.
- **Harness** (`docs/plans/PLAN_HARNESS.md`): done. One run works the whole queue, or a capped part of it, through a to-do list shown on the queue page. Each claim gets a fresh claim agent. The agent looks up sections of the policy wording and the claims manual, and the claim page shows what it read. A claim that fails goes back in the queue; one that fails twice is left for a handler ("Needs a handler"). Default model: DeepSeek V4.1 Flash, low reasoning effort.
- Not built: sign-in, real payments, messages to customers, stopping a run midway.

## Setup

```bash
# backend (Python 3.11+)
cd backend
python -m venv .venv
./.venv/Scripts/python -m pip install -e ".[dev]"     # Windows; use .venv/bin/python elsewhere

# frontend and e2e
cd ../frontend && npm install
cd ../e2e && npm install && npx playwright install chromium
```

Create a `.env` in the repo root (git-ignored) with your key:

```
OPENROUTER_API_KEY=...
```

The model and how it thinks are set in **`agent.toml`** at the repo root (committed, no secrets):

```toml
[agent]
model = "deepseek/deepseek-v4.1-flash"   # any OpenRouter model id
temperature = 0
reasoning_effort = "low"                 # "low", "medium" or "high"
max_attempts = 2                         # failed attempts before a claim is left for a handler
# step_retries = 2                       # retries of one model call (timeout, provider error)
# claim_timeout_s = 300                  # time limit for one attempt at one claim
# max_steps = 40                         # steps for one claim
# run_max_steps = 400                    # steps for a whole run
```

The environment overrides the file (`AGENT_MODEL`, `AGENT_TEMPERATURE`, `AGENT_REASONING_EFFORT`, also usable in `.env`), so a value left in `.env` hides the one in `agent.toml`. `AGENT_PROVIDER=scripted` swaps the model for a deterministic fake (used by the tests). `DB_PATH` and `DATA_DIR` are optional.

## Run

All the commands you need every day (start, auto-reload, tests, reset) are in **[COMMANDS.md](COMMANDS.md)**.

```bash
cd backend && ./.venv/Scripts/python -m uvicorn app.main:app --port 8000
cd frontend && npm run dev          # http://localhost:5173, proxies /api to the backend
```

Screens: **Claims** (the queue with the agent's to-do list and a run limit, and claim detail with the agent's live activity and what it read), **Approvals** (held payouts) and **Agent instructions** (for running the queue and for working a claim).

To try the agent cheaply, set **Limit** to 5 on the queue page before pressing **Run queue**: the agent works five claims and leaves the rest waiting.

## Test

```bash
npm run check                                          # everything below, in order, from the repo root
cd backend && ./.venv/Scripts/python -m pytest        # unit tests (the real-model smoke test runs only with RUN_LIVE_SMOKE=1)
cd frontend && npm test -- --run                       # component tests
cd e2e && npm test                                     # end-to-end, scripted agent, throwaway database
```

No test calls a model. The scripted agent stands in for it, and fails on purpose on two claims so recovery can be tested: CLM-1013 always fails and is left for a handler, and CLM-1017 fails once and is then decided.

## Clear the analyses and test again

To forget every analysis the agent made (decisions, activity logs, payments, runs and to-do lists) and put all 25 claims back in the queue. Do this before any live run with the real model:

```powershell
# from the backend folder
.\.venv\Scripts\python ..\scripts\reset_db.py

# or from the repo root
.\backend\.venv\Scripts\python scripts\reset_db.py
```

It is safe while the backend is running: reload the page and every claim is "Waiting" again. The claims themselves are kept. To start from a completely empty database file instead, stop the backend and delete `farting.db`, `farting.db-wal` and `farting.db-shm` in the repo root; the next start recreates it.

## Score the agent

`scripts/score.py` is the only code that reads `data/evals/`; the app never does.

```bash
cd backend
./.venv/Scripts/python ../scripts/score.py --split queue --run            # run the agent on the queue, then score
./.venv/Scripts/python ../scripts/score.py --split queue --run --tiny     # the 5 tiny claims, cheap
./.venv/Scripts/python ../scripts/score.py --split held_out               # the held-out set (runs the agent)
```

### Results (queue, 25 claims, 2026-10-29)

| Model | Decision accuracy | Payout accuracy |
|---|---|---|
| `openai/gpt-4o-mini` | 52% | 28% |
| `openai/gpt-4o` | 60% | 52% |

Both models approve claims the handlers declined or referred. Of the 6 expected referrals, 3 need the claims manual or the customer history, which the pilot left out on purpose, so some misses were expected. The harness now gives the agent the manual; it has not been scored yet. The held-out set has not been scored yet.

## Layout

```
backend/   FastAPI + SQLite + LangChain agent (app/), tests (tests/)
frontend/  React (Vite, TypeScript), Vitest
e2e/       Playwright
scripts/   score.py, reset_db.py, check.mjs
data/      seed data; data/evals and data/security are off limits to the app
docs/      SPEC, TECH_STACK, DESIGN; plans/ holds the finished plans and the harness brief
labs/, lab1_desk/, lab2_desk/   course notebooks and lab desks (each desk has its own .venv); not part of the app
```
