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

This starts the three parts, with `.env` in their environment:

| Part | Address | Reloads on a code change |
|---|---|---|
| Backend | http://127.0.0.1:8000 | Yes |
| The agent's service | http://127.0.0.1:8002 (`UNDERCOVER_AGENT` to change where the backend looks) | No: a reload would stop the claim it is working on |
| Dashboard | http://localhost:5173 | Yes |

Ctrl-C stops all three. If one dies, the others stop too and the output says which. Everything `npm run dev` starts must work on Windows.

`cd backend; uv run -m agent CLM-1001` works one claim in its own process, with no run. Dispatches and evaluations start from the dashboard, through the service.

## Live runs

A run with the real model spends money and writes to Supabase and Langfuse. Before any of them:

1. Reseed once: `npm run seed`.
2. Keep to the runs the brief names, for this change those in `docs/SWARM.md`: a dispatch at N=3 stopped partway, a dispatch at N=25, an evaluation of the evaluation set at N=25 and one of the held-out set's five tiny claims at N=5. Evaluations work copies and need no reseed.

The free plan allows 15 connections at once through the session pooler, across every process. Each of our processes uses at most 4. A notebook left open with a pool from the labs counts too, so close the labs before measuring.
