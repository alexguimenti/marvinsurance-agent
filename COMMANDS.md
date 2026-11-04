# Commands

Everything below is PowerShell. Commands start from the repo root (`undercover_mex`) unless they begin with `cd`, or say which folder they run from.

## Open the app (every time)

From the repo root, in one terminal:

```powershell
npm run dev
```

It starts the backend (http://127.0.0.1:8000), the agent's service (http://127.0.0.1:8002) and the dashboard, with `.env` in their environment. Open http://localhost:5173. Ctrl-C stops all three.

With `DATABASE_URL` in `.env` the records are on Supabase, which is slower: each step goes over the network. Without it they are in `farting.db`.

### What restarts on its own

| You change | What happens |
|---|---|
| a `.py` file in `backend/app/` | the backend restarts by itself |
| a `.py` file in `backend/agent/`, `agent.toml` or `.env` | stop and start `npm run dev` |
| `backend/agent/instructions_run.md` or `instructions_claim.md` (the agent's prompts) | nothing to restart: they are read again for every run and every model call |
| files in `frontend/src/` | the page updates by itself |

If the agent stops during a run, nothing is lost. Press **Run queue** or use `uv run -m agent`: the claim carries on from its last step and the run is finished, with its own limit. After a hard kill, wait 45 seconds first.

## Start a run from the terminal

```powershell
cd backend
uv run -m agent --limit 5
uv run -m agent --claims CLM-1003,CLM-1011
```

It reads `.env`. With the service up, the service does the work; otherwise this process does.

## Run without calling a model (free)

Uses a fake model, and SQLite as long as there is no `DATABASE_URL` in the environment. Good for trying the screens. In `.env`, set `AGENT_PROVIDER=scripted` and take out `DATABASE_URL`, then `npm run dev`.

## Reseed and test again

Makes our tables again, forgets the checkpoints and every analysis, and puts all 25 claims back in the queue, on the database in `.env`. Do this before any live run with the real model, and set **Limit** to 5 on the queue page so the trial costs little. Have no agent working when you do. It also brings an older `farting.db` up to date.

```powershell
npm run reseed
```

## Tests

```powershell
npm run check                                           # all of them, from the repo root, stopping at the first failure
cd backend;  .\.venv\Scripts\python -m pytest          # backend
cd frontend; npm test -- --run                          # frontend
cd e2e;      npm test                                   # end to end (own ports 8100, 8003 and 5273, own database)
```

The end-to-end run does not touch the servers or the database you use every day.

## Score the agent

Runs the agent on the queue and compares it with the handlers' decisions. Calls the model, so it costs a few cents.

```powershell
cd backend
.\.venv\Scripts\python ..\scripts\score.py --split queue --run --tiny   # 5 claims, cheap
.\.venv\Scripts\python ..\scripts\score.py --split queue --run          # all 25
.\.venv\Scripts\python ..\scripts\score.py --split held_out             # the held-out set
```

## Install from scratch

```powershell
cd backend;  uv sync --extra dev
cd frontend; npm install
cd e2e;      npm install; npx playwright install chromium
```

The model is chosen in `agent.toml`. `.env` holds `OPENROUTER_API_KEY`, `DATABASE_URL` and the `LANGFUSE_*` keys.
