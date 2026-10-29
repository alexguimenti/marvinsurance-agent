# Marvin FartInsurance: Farting Agent pilot

A course project (nothing here is real). The Farting Agent works a travel-insurance claims queue like a human analyst: it reads each claim, the policy, the customer's history and the policy wording, then approves, declines or refers it. Payouts above $1,000 wait for a handler. See `docs/SPEC.md` for what we build, `docs/TECH_STACK.md` for the stack and `docs/PLAN.md` for the implementation plan.

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

Create a `.env` in the repo root (it is git-ignored):

```
OPENROUTER_API_KEY=...
AGENT_MODEL=openai/gpt-4o-mini     # any OpenRouter model id; change it to switch models
AGENT_TEMPERATURE=0
```

`AGENT_PROVIDER=scripted` swaps the model for a deterministic fake (used by the tests). `DB_PATH` and `DATA_DIR` are optional.

## Run

```bash
cd backend && ./.venv/Scripts/python -m uvicorn app.main:app --port 8000
cd frontend && npm run dev          # http://localhost:5173, proxies /api to the backend
```

Screens: **Claims** (queue and claim detail with the agent's live activity), **Approvals** (held payouts) and **Agent instructions**.

## Test

```bash
cd backend && ./.venv/Scripts/python -m pytest        # unit tests (a real-model smoke test runs only if a key is set)
cd frontend && npm test -- --run                       # component tests
cd e2e && npm test                                     # end-to-end, scripted agent, throwaway database
```

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

Both models approve claims the handlers declined or referred. Of the 6 expected referrals, 3 need the claims manual or the customer history, which the pilot leaves out on purpose, so some misses are expected. The held-out set has not been scored yet.

## Layout

```
backend/   FastAPI + SQLite + LangChain agent (app/), tests (tests/)
frontend/  React (Vite, TypeScript), Vitest
e2e/       Playwright
scripts/   score.py
data/      seed data; data/evals and data/security are off limits to the app
docs/      SPEC, TECH_STACK, DESIGN, PLAN
```
