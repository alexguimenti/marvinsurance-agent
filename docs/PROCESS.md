# How we work

## Planning

A change starts from a brief. The plan goes in `docs/` and is ticked off there as the work goes on; finished plans move to `docs/plans/`. A plan says which files change, the names and signatures others rely on, and the tests that prove each task.

## Building

- Write the test first and watch it fail, then write the code.
- Commit each finished task. Tests never call a model: a test that tries is stopped by `backend/tests/conftest.py`.
- The checks are one command, from the repo root:

  ```powershell
  npm run check
  ```

  It runs the backend tests, the front-end tests, the type check, the lint and the end-to-end tests, in that order, stopping at the first failure. It needs no `.env`, Supabase, Langfuse or OpenRouter. To prove that, run it in a clean `git worktree`, where `.env` does not exist.

## Running

```powershell
npm run dev
```

This starts the five parts, with `.env` in their environment:

| Part | Address | Reloads on a code change |
|---|---|---|
| Backend | http://127.0.0.1:8000 | Yes |
| The context layer | http://127.0.0.1:8006/mcp (`UNDERCOVER_CONTEXT` to change where the agent looks) | No |
| The agent (an AgentCore app) | http://127.0.0.1:8080 (`UNDERCOVER_AGENT` to change where the backend looks) | Yes: a reload stops the work it is doing, and the next run carries on |
| The Copilot (an AgentCore app) | http://127.0.0.1:8081 (`UNDERCOVER_COPILOT` to change where the backend looks) | Yes: a reload stops the turn it is working on, and the handler sends a new message |
| Dashboard | http://localhost:5173 | Yes |

Ctrl-C stops all five. If one dies, the others stop too and the output says which. Everything `npm run dev` starts must work on Windows.

`cd backend; uv run -m agent CLM-1001` works one claim in its own process, with no run. Dispatches and evaluations start from the dashboard: the app creates the run and asks the agent to work it.

## Building the dashboard

```powershell
npm run build
```

This writes the dashboard to `dashboard/dist` (see `TECH_STACK.md`, Deployment). With that folder in place the backend serves it at `http://127.0.0.1:8000`: `uvicorn backend.main:app --port 8000` from the repo root, with `.env` in its environment.

## The agent on port 8080

The agent listens on 8080, the port AgentCore apps use. Lab 1's `agentcore dev` serves on 8080 too: stop it before `npm run dev`. With `npm run dev` stopped, the agent can be served on its own, as Runtime starts it, from the repository root:

```powershell
$env:PYTHONPATH = "backend"; uv run --directory . --project backend --env-file .env --isolated --only-group runtime python -m runtime.main
```

Posting `{"operation": "health"}` to `http://127.0.0.1:8080/invocations` answers `{"healthy": true}` (the health check needs no key). Every other operation needs `Authorization: Bearer <UNDERCOVER_API_KEY>`.

## The context layer and the Gateway

`npm run dev` starts the context layer beside the others, and `cd backend; uv run -m context` starts it alone. With no `UNDERCOVER_GATEWAY_URL` and no `UNDERCOVER_MEMORY_ID` in `.env` the agent calls the app direct and the context layer at 8006, with no memory tool. With them set (the deployment writes both into `.env`) it needs the AWS CLI's credentials and region, calls its tools through the Gateway and remembers each customer. `npm run seed` loads the documents from `backend/context/`.

## The agent's key

`UNDERCOVER_API_KEY` in `.env` is the agent's key to the backend, at least 32 random characters (`python -c "import secrets; print(secrets.token_urlsafe(32))"`). `npm run dev` passes it to the backend and the agent, and `uv run -m agent CLM-1001` loads it from `.env`; the backend must be running. `UNDERCOVER_BACKEND` says where the backend is (`http://127.0.0.1:8000` by default).

## Live runs

A run with the real model spends money and writes to Supabase and Langfuse. Before any of them:

1. Reseed once: `npm run seed`.
2. Keep to the runs the brief names, for this change those in `docs/specs/CONTEXT.md` (two claims, a dispatch at N=25 and an evaluation of the five tiny claims, on the direct route with memory off); for the improver those in `docs/specs/IMPROVE.md`: two rounds of the improver, signed in as a handler, and one dispatch at N=25 on the version they leave current. Evaluations work copies and need no reseed. Earlier changes' runs are in `docs/specs/SWARM.md`.
3. Check the credit limit on the OpenRouter key first: a round's evaluations, the improver and the judge all spend on it. Each round has a spending limit of its own (50 cents to start with), and the dashboard says what a round may spend before it starts.

The free plan allows 15 connections at once through the session pooler, across every process. Each of our processes uses at most 4. A notebook left open with a pool from the labs counts too, so close the labs before measuring.

Handlers sign in with Supabase Auth. The live checks sign in as Dana Whitfield (and, for the rights, as Marcus Oyelaran) with `HANDLER_PASSWORD`; `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` in `.env` carry the same values as `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`. `scripts/check_live.py --round N` shows a round's stages, proposals, verdict and cost, and `--session round-N` its traces in Langfuse.

## The Copilot on port 8081

The Copilot, the handlers' assistant in the dashboard's sidebar, is an AgentCore app of its own. On the laptop `npm run dev` serves it on 127.0.0.1:8081 and asks for no key: every call it makes to the app carries the sign-in of the handler it works for. With `UNDERCOVER_COPILOT_MEMORY_ID` unset it has no memory, and with `UNDERCOVER_GATEWAY_URL` unset it reaches the context layer direct, as the claim agent does. With `npm run dev` stopped, it can be served on its own, as Runtime starts it, from the repository root:

```powershell
$env:PYTHONPATH = "backend"; $env:PORT = "8081"; uv run --directory . --project backend --env-file .env --isolated --only-group runtime python -m runtime.copilot
```

Posting `{"operation": "health"}` to `http://127.0.0.1:8081/invocations` answers `{"healthy": true}`. `npm run check` proves the same through the staged package (`runtime/` with `agent/` beside it, only the `runtime` group installed). The live checks for the Copilot are those in `docs/specs/COPILOT.md`: one `npm run seed`, `npm run dev` on the default model, on the direct route with memory off, signed in as Dana Whitfield through `e2e/live_security.mjs` (`copilot:<message>` and `confirm:sign_off`), so the password never passes through a tool call.
