# Commands

Everything below is PowerShell. Commands start from the repo root (`undercover_mex`) unless they begin with `cd`, or say which folder they run from.

## Open the app (every time)

From the repo root, in one terminal:

```powershell
npm run dev
```

It starts the backend (http://127.0.0.1:8000), the agent's service (http://127.0.0.1:8002) and the dashboard, with `.env` in their environment. Open http://localhost:5173 and sign in as a handler (Dana Whitfield, Marcus Oyelaran or Priya Raman, with `HANDLER_PASSWORD`). Ctrl-C stops all three.

With `DATABASE_URL` in `.env` the records are on Supabase, which is slower: each step goes over the network. Without it they are in `marvin.db`.

### What restarts on its own

| You change | What happens |
|---|---|
| a `.py` file in `backend/app/` | the backend restarts by itself |
| a `.py` file in `backend/agent/`, `agent.toml` or `.env` | stop and start `npm run dev` |
| `backend/agent/instructions_claim.md` (version 1 of the claim agent's instructions) | `npm run seed`: it is copied into version 1 when the tables are made. A later version's instructions are in the database, and a handler approves them |
| files in `frontend/src/` | the page updates by itself |

If the agent stops during a dispatch, nothing is lost. When `npm run dev` starts again the dispatch shows as stopped; press **Start dispatch** and the claims left in progress carry on from their last step, as the same attempts.

## Work one claim from the terminal

```powershell
cd backend
uv run -m agent CLM-1003
```

It reads `.env` and works the claim in this process, with no run: it carries it on if it is in progress, or starts it if it is waiting.

## Run without calling a model (free)

Uses a fake model, and SQLite as long as there is no `DATABASE_URL` in the environment. Good for trying the screens. In `.env`, set `AGENT_PROVIDER=scripted` and take out `DATABASE_URL`, then `npm run dev`.

## Reseed and test again

Makes our tables again, forgets the checkpoints, every analysis and every evaluation, and puts all 25 claims back in the queue, on the database in `.env`. Do this once before the live runs with the real model. Have no agent working when you do. It also brings an older `marvin.db` up to date. `npm run reseed` is the same command.

```powershell
npm run seed
```

## Tests

```powershell
npm run check                                           # all of them, from the repo root, stopping at the first failure
cd backend;  .\.venv\Scripts\python -m pytest          # backend
cd frontend; npm test -- --run                          # frontend
cd e2e;      npm test                                   # end to end (own ports 8100, 8003, 8004, 8005 and 5273, own database)
```

The end-to-end run does not touch the servers or the database you use every day.

## Score the agent

On the **Scoreboard** page, choose the set (the evaluation set is the queue's 25 claims; the held-out set is 25 others), tick **Only the five tiny claims** for a cheap check, choose N and press **Start evaluation**. It works copies, so the queue stays as it was and nothing needs a reseed. It calls the model, so it costs a few cents.

## Improve the claim agent

On the **Improvement** page, as a handler, press **Start round**. The dialog says what the round may spend. The improver evaluates the current version, proposes a change and waits for a senior handler to approve or reject it; code then keeps the version or rolls it back. It calls the model, the improver and the judge, so a round costs cents, up to its spending limit. **Settings** holds the limits, and **Configuration** the versions and what changed between them.

```powershell
cd backend; uv run python ../scripts/check_live.py --round 1            # a round's stages, proposals, verdict and cost
cd backend; uv run python ../scripts/check_live.py --session round-1    # its traces in Langfuse
```

## Install from scratch

```powershell
cd backend;  uv sync --extra dev
cd frontend; npm install
cd e2e;      npm install; npx playwright install chromium
```

The model is chosen in `agent.toml`, or with `MODEL_NAME` in the environment. `.env` holds `OPENROUTER_API_KEY`, `DATABASE_URL`, the `LANGFUSE_*` keys, `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` (for sign-in) and `HANDLER_PASSWORD`.

---

# First-time setup, tests, reseeding and scoring

(Moved here from the README.)

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
