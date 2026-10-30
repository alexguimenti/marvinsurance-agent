# Commands

Everything below is PowerShell. Commands start from the repo root (`undercover_mex`) unless they begin with `cd`, or say which folder they run from.

## Open the app (every time)

You need two terminals.

**Terminal 1: backend** (http://127.0.0.1:8000)

```powershell
cd backend
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000 --reload --reload-dir app --reload-dir .. --reload-include "*.py" --reload-include agent.toml --reload-include .env
```

**Terminal 2: frontend** (http://localhost:5173)

```powershell
cd frontend
npm run dev
```

Open http://localhost:5173.

### What restarts on its own

| You change | Backend |
|---|---|
| a `.py` file in `backend/app/` | restarts by itself |
| `agent.toml` (model, temperature, reasoning) | restarts by itself |
| `.env` (the API key) | restarts by itself |
| `backend/app/instructions_run.md` or `instructions_claim.md` (the agent's prompts) | **does not**: restart with Ctrl+C and run the command again |
| files in `frontend/src/` | the page updates by itself, nothing to restart |

The `--reload-include` options need the `watchfiles` package, which `pip install -e ".[dev]"` installs. Without it uvicorn ignores them and only reloads `.py` files. The simple form, `python -m uvicorn app.main:app --port 8000 --reload`, reloads `.py` files only and is enough if you never touch `agent.toml`.

Careful: if the server restarts during a run, the run stops. On the next start the run is marked failed and the claim it was working on goes back to "Waiting"; claims already decided stay decided. Start a new run for the rest.

## Run without calling a model (free)

Uses a fake agent. Good for trying the screens.

```powershell
cd backend
$env:AGENT_PROVIDER = "scripted"
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000 --reload
```

## Clear the analyses and test again

Puts all 25 claims back in the queue and deletes the decisions, activity logs, payments, runs and to-do lists. Safe while the backend is running; reload the page afterwards. Do this before any live run with the real model, and set **Limit** to 5 on the queue page so the trial costs little.

From the `backend` folder (where the backend terminal already is):

```powershell
.\.venv\Scripts\python ..\scripts\reset_db.py
```

From the repo root:

```powershell
.\backend\.venv\Scripts\python scripts\reset_db.py
```

## Tests

```powershell
npm run check                                           # all of them, from the repo root, stopping at the first failure
cd backend;  .\.venv\Scripts\python -m pytest          # backend
cd frontend; npm test -- --run                          # frontend
cd e2e;      npm test                                   # end to end (own ports 8100 and 5273, own database)
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
cd backend;  python -m venv .venv; .\.venv\Scripts\python -m pip install -e ".[dev]"
cd frontend; npm install
cd e2e;      npm install; npx playwright install chromium
```

The model is chosen in `agent.toml`; the OpenRouter key goes in `.env` as `OPENROUTER_API_KEY=...`.
