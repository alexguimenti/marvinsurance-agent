# Status

Last updated 2026-11-09, after the improver and sign-in (`docs/PLAN_IMPROVE.md`). Its live verification is only partly done: see "Live verification of the improver (2026-11-09)" below.

## What works

- The queue, claim detail, the agent's live activity, approvals, the Scoreboard, Improvement, Configuration and Settings, on `npm run dev`, behind a sign-in.
- **Sign-in.** Handlers sign in with Supabase Auth; the backend checks the token on every action, and only Dana Whitfield may sign off over the limit, decide a proposal or change a setting.
- **Settings.** The approval limit, the objectives, noise and tolerance, the round spending limit and N, each with who changed it and when. A decision keeps the limit it was made under. Light and dark appearance.
- **Versions.** The claim agent's instructions, model and effort are kept in versions, as is the improver's recipe; each run records the version it worked with.
- **The judge.** Jev grades each claim's explanation against the handler's reason; the grade shows on the Scoreboard and in an evaluation.
- **The improver.** Rounds of first evaluations, a proposal, a handler's decision, an evaluation of the new version and a verdict by code; a spending limit per round; a round waiting for a handler holds nothing; rounds left working show as stopped after a restart.
- **Dispatches.** A dispatcher with no model works every claim left in progress and every waiting claim, oldest submitted first, with N claim agents at once (1 to 25, 3 to start with). The run panel shows each claim and where it stands, the dispatch's model cost and agent time, and, when it ends, a summary from the records.
- **Problems are put right.** A rate limit or a timeout waits and carries the claim on from its last step, as the same attempt; still failing after its waits, it stays in progress for the next dispatch. Any other error puts the claim back in the queue and the same dispatch tries it once more; a claim that fails twice is left for a handler. One bad claim never stops a dispatch.
- **One run at a time.** A second start of a dispatch or an evaluation is refused. When the agent's service starts, a run left running shows as stopped, and the next dispatch carries on its claims.
- **Cost and time.** Every model call's tokens and cost, and every stretch of agent time, are recorded with their dispatch or evaluation, or with no run. A wait for a sign-off is not counted.
- **Evaluations.** On copies of the evaluation set (the queue's 25 claims) or the held-out set, whole or their five tiny claims. Copies never show in the queue, are never carried on by a dispatch and cannot be signed off; a held approval counts as finished, and a finished copy's progress is cleared. The Scoreboard lists them with decision accuracy, accuracy, the cost of mistakes, straight-through rate, cost and time per claim, and the time for the run, and opens each to its claims beside the handlers'.
- **Sign-offs** still pay within seconds through the service, and nothing is ever decided or paid twice.
- **Langfuse:** each claim keeps its session per thread, and each trace carries the tag `run-<id>` of its dispatch or evaluation.
- Default model: DeepSeek V4.1 Flash with low reasoning effort (the owner kept it in place of GPT-6 Luna). `MODEL_NAME` chooses another, with no effort.

## Checks

`npm run check` in a clean worktree with no `.env`: 277 backend tests (1 skipped), 66 front-end tests, the type check, the lint and 9 end-to-end tests, all passing.

## Live verification (2026-11-05)

On Supabase and the default model, after one `npm run seed`, through the Playwright MCP server. $0.075 on OpenRouter in all.

| Check | Result |
|---|---|
| A dispatch at N=3 | Oldest submitted first, never more than 3 at once. Each claim took 14 to 26 s |
| Stopped partway with a forced kill of the process on 8002 | `npm run dev` stopped the backend and the dashboard too. After a restart the run showed as stopped. 8 claims were finished, 3 held, 2 in progress |
| A dispatch at N=25 then | The 2 in progress carried on as the same attempts (`CLM-1004-1`, `CLM-1023-1`, attempts 0), with one decision each. The other 15 waiting claims started with them: all 17 at once, started within 14.5 s of each other |
| Its run against the sum of its claims | 94 s for the run; 1,071 s of agent time summed over its claims |
| Each claim, against N=3 | **Slower: 32 to 84 s, against 14 to 26 s.** See below |
| A sign-off | Paid 2.8 s after the click on the approvals page (signed off at 1.3 s), and "Paid ... signed off by Dana Whitfield" on the claim's page |
| No claim fails for want of a connection | None did. No pool timeout in the logs |
| At most 12 connections for user postgres | 10 at the most, polled every 8 to 13 s during the dispatch and the evaluations. Read with SQL from our own connection, not through the Supabase MCP server, which this session did not have |
| An evaluation of the evaluation set at N=25 | Finished in 128 s: decision accuracy 92%, accuracy 88%, mistakes $360, straight-through 84%, $0.0012 and 81 s per claim |
| The held-out set's five tiny claims at N=5 | Finished in 38 s: decision accuracy 100%, accuracy 80%, mistakes $70, straight-through 80%, $0.0016 and 25 s per claim |
| The queue's counts before and after the evaluations | The same: 23 decided, 2 awaiting approval |
| The N=25 dispatch in Langfuse by its tag | 17 traces tagged `run-2`, one for each of its claims |

### Where the time goes at N=25

Each claim does dozens of short statements: its trace events (302 in that run), one row per model call (58), the claim's own reads and writes, and a checkpoint after every step, with its writes. Every statement is a trip of about 213 ms to Supabase, and the process has 2 connections for records and 2 for checkpoints. With 17 claim agents at once, they spend much of their time waiting for one of those 4, not for the model. This is an estimate from the counts and the trip time, not a profile. Ways to win it back, keeping to 4 connections: fewer trace events per step, or writing a step's events in one statement. Saving fewer checkpoints would also help, but would weaken carrying on after a restart.

## Live verification of the improver (2026-11-09)

Partly done. The owner stopped it to start `docs/PLAN_SECURE.md`. On Supabase and the default model, signed in as Dana Whitfield and Marcus Oyelaran. About $0.25 on OpenRouter in all (round 1's evaluations $0.13 and round 2's $0.09, with the improver's and judge's calls). This is an estimate from the round totals.

| Check | Result |
|---|---|
| Round 1 (limit $0.50), 1 evaluation-set run and 2 held-out runs of version 1 | Finished. Held-out scores of version 1: accuracy 0.86, grade 1.31, decision accuracy 0.92, $0.0017 and 105 s per claim, straight-through 0.84 |
| Round 1's version 2 | **Rolled back:** the round could not afford to evaluate it, about $0.84 against $0.37 left. Version 1 stayed current |
| Round 2 (limit raised to $1.00 as Dana) | The first proposal was rejected with a reason; the improver's next proposal was about a failure shared by the two wrong claims (an account of a fact the wording requires a document for), not about one claim type. It was approved and saved as version 3. The round was **stopped by the owner** during its third evaluation, so it has no verdict |
| Marcus Oyelaran starting a round or changing a setting | 403 on each |
| Handlers' sign-off, one dispatch at N=25, Langfuse sessions `round-1` and `round-2`, the design review | **Not done** |
| Screenshots at 1280x800, light and dark | Taken; the Improvement page and the Scoreboard's controls read well in both. The Scoreboard and Configuration were still loading in the dark and light shots, taken while a round used the connections; not re-taken |
| Two held-out runs of version 1 against noise and tolerance | Not reported: the per-measure differences were not computed |

**Finding:** the spending estimate follows the brief (an evaluation's claims times the highest cost per claim seen for its model). Here the highest claim cost $0.011 against a mean of $0.0017, so a whole new version is estimated at $0.84. With the starting limit of $0.50 and the first evaluations already spent, no new version can be afforded on this model, and every round rolls back. A limit of about $1.00 lets it through; the real cost of version 3's three evaluations was about $0.13.

## Security changes before AWS (2026-11-10)

Built, from `docs/PLAN_SECURE.md`: every route under `/api` with the agent's API key (`X-API-Key`, constant-time compare) on everything the agent's side calls; the agent reaches the backend only over HTTP (`agent/connection.py`, `api.py`, `improver_api.py`), keeping one database link, LangGraph's checkpoints; a second model (GPT-6 Luna at `none`, or `MODEL_NAME`) screens each claim before the claim agent's first model call, outside the versioned configuration, with CLM-1901 seeded into the queue (26 claims); `GET /api/agent/openapi.json` with the eight operations; a payment limit checked first (403 naming it); a sign-off that pays in the backend; `npm run build`, `backend.main` (`app` and a Mangum `handler`), the dashboard served at `/`, `NullPool` and schema-qualified statements on Lambda, and the `server` dependency group with its check.

`npm run check` passed on 2026-11-10 in a clean `git worktree` with no `.env`, in pieces because the system stopped the one-command run twice for memory: backend 480 passed and 1 skipped (16 minutes), the server-group import, the front end (131 tests, types, lint) and the 14 end-to-end tests. A quoting error in the server-group step of `scripts/check.mjs` was fixed on the way.

**Not done:** the final whole-branch review by a fresh reviewer.

## Live checks of the security changes (2026-11-10)

On Supabase and the default model (`deepseek/deepseek-v4.1-flash` for the claim agent, `openai/gpt-6-luna` at `none` for the check, through our key), after one `npm run seed`. Model spend in all: about $0.08 on OpenRouter (the dispatch $0.07, the evaluation about $0.002 and the check's own calls $0.0001 a claim).

| Check | Result |
|---|---|
| `curl -X POST /api/claims/CLM-1001/pay` with no key; `GET /api/claims` with no sign-in | 401 and 401 |
| `uv run -m agent CLM-1001` | `CLM-1001: approve $420.00, paid` |
| A dispatch (N=5) of the rest of the queue | Finished: 15 approved, 5 declined, 5 referred, 3 held for sign-off (CLM-1003, CLM-1008, CLM-1011) and CLM-1021 held too. **CLM-1901 referred** by the check, which the claim shows: decision REFER $0.00, the reason quoting the instruction, "Security check" as its section, decided by `openai/gpt-6-luna`, one model call at $0.0001 |
| An evaluation of the evaluation set's tiny five (N=5) | Finished in 1 min 24 s: 100% decision accuracy, 80% accuracy, $0.0016 a claim. **None flagged** by the check: every rationale is the claim agent's own |
| Dana signs off CLM-1003 on the dashboard | Paid once, in the one request: `approved_by` HND-01, `paid_at` set, claim decided, no agent service involved; the other held payouts stayed held |
| The built dashboard, the backend alone (`uvicorn backend.main:app`, with the key and `.env` in its environment) | Dana signed in at http://127.0.0.1:8000 and saw the 26 claims |
| The backend as Lambda sees it (`AWS_LAMBDA_FUNCTION_NAME` set, `DATABASE_URL` on port 6543, lifespan off) | A signed-in handler loaded the queue and two claim pages in a row, four requests in all, with no error in the log |
| The deployment package (Linux wheels for Python 3.12, `uv pip install --python-platform x86_64-manylinux2014`) | **21.4 MB zipped**, 76 MB unzipped, with `backend/` (no tests, caches, `.venv` or egg-info), `data/` and `dashboard/dist/` |

Notes:
- Packaging by the lab's cells copies the whole `backend/` folder, which on a laptop holds `.venv` (and egg-info): leave those out, as the 21.4 MB figure does.
- The first time the backend alone was started the sign-in worked only after its startup finished (it opens its connections to Supabase first, a few seconds).
- The live checks were driven by `e2e/live_security.mjs`, which signs in as Dana through the dashboard with the password from `.env` in that process only. A `#` in `HANDLER_PASSWORD` needs the value in quotes in `.env`, or Node's `.env` reader cuts it.
- The agent working the plan did not use the Playwright MCP server for the sign-in at 8000: the password would have had to pass through a tool call, so the script route stood in (a ruling).
- The sign-off check is for CLM-1003 only; the three other held payouts and CLM-1021's were left held.

## Known limits

- Supabase is far from where this runs: about 220 ms a trip and 6.5 s for a new connection, so connections open at startup. With many claim agents sharing a process's 4 connections, they queue for them: each claim took about three times as long at N=25 as at N=3 (see above).
- A second attempt at a claim has a new thread, so a new Langfuse session. The claim id tag finds them all.
- After a reseed, thread names repeat, so an old Langfuse session gains new traces.
- `AGENT_MODEL` is still read, from the field's name, but `MODEL_NAME` wins over it.
- Stopping `npm run dev` with Ctrl-C was not exercised by a real keypress; stopping when one part dies was.

## Not built

Real payments, messages to customers, rejecting a held payout.
