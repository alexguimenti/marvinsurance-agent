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
