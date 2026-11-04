# Status

Last updated 2026-11-04, after the durable agent (`docs/PLAN_DURABLE.md`).

## What works

- The queue, claim detail, the agent's live activity, its instructions and approvals, on `npm run dev`.
- One run plans a to-do list and hands each claim to a fresh claim agent, capped or limited to chosen claims.
- The claim agent is a hand-written LangGraph graph. An approval over $1,000 stops and waits for a handler. A sign-off on the dashboard reaches the agent's service, which picks the claim up and pays it. The payment records who signed off and when.
- Work survives a restart. A claim stopped partway carries on from its last step, with the same thread and attempts. A stopped run is finished by the next one.
- Nothing happens twice: a repeated decision or payment counts as done, and a different decision is refused.
- Records and checkpoints on Supabase, in the schema `undercover`. Traces in Langfuse, one session per claim and thread.

## Live verification (2026-11-04)

On Supabase and the default model, `deepseek/deepseek-v4.1-flash`, reseeded before each run.

| Check | Result |
|---|---|
| A run capped at five works five | Yes. 3.5 min and $0.0075 on the final code. It took 8.5 min before the speedup below |
| A claim over the limit waits | Yes, CLM-1003, CLM-1008 and CLM-1011 |
| Signed off on the dashboard, paid within five seconds | Yes with the claim's page open: 2.6 s, 2.7 s and 3.0 s from the sign-off to "Paid ... signed off by ..." on screen. During a run, 4.4 s from the click to the payment showing in the API. Moving from the approvals page to the claim's page right after signing took about 6 s, page load included |
| A run killed partway carries on | Yes. CLM-1001 kept thread `CLM-1001-1` and attempts 0, with one decision. The same run finished, with its cap of five |
| Before and after a sign-off in one Langfuse session | Yes: `CLM-1011:CLM-1011-1` holds both, read through Langfuse's API |
| No more than 12 connections for user postgres | **Not met as written: the peak was 15.** See below |

### Connections

The peak of 15 in `pg_stat_activity` was made up of:

- 1 from `pg_net`, Supabase's own.
- 4 from a Jupyter kernel with the lab's pool, open since before the work began (connections from 14:51 UTC, last running the lab's own queries). It was left alone, because it may hold unsaved work.
- Up to 10 through the pooler for our processes and the measuring script. This count also holds connections that Supavisor kept open after earlier processes were killed.

Our processes are capped in code at 8 (backend 4, agent 2 + 2), or 12 with `uv run -m agent` beside them. A test covers the pool settings. To measure it as the brief asks, close the lab notebooks, wait for the pooler to let go of old connections, and run `python scripts/check_live.py` during a run.

The count was read with SQL from our own connection, not through the Supabase MCP server, which was not loaded in the session that did the work. `.mcp.json` is now at the repo root, so a session opened there gets it after signing in with `/mcp`.

## Known limits

- Supabase is far from where this runs: about 220 ms per trip, and 6.5 s for a new connection. Connections open at startup for that reason.
- A second attempt at a claim has a new thread, so a new Langfuse session. A tag with the claim id finds them all.
- After a reseed, thread names repeat, so an old Langfuse session gains the new traces.
- After a hard kill, a run can be finished only once its heartbeat is 45 seconds old.
- While the service is up it looks for signed-off, unpaid claims every 30 seconds, in case the call from the backend was lost. If the service is down they wait for it to start, or for the next run.
- Stopping `npm run dev` with Ctrl-C was not exercised by a real keypress; stopping when one part dies was.

## Not built

Sign-in, real payments, messages to customers, rejecting a held payout.

## Accuracy

Not scored since the claim agent became a graph. `scripts/score.py --split queue --run --tiny` checks it against the handlers' decisions, and costs a few cents.
