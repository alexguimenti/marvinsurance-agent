# Status

Last updated 2026-11-15, after the Copilot (`docs/plans/PLAN_COPILOT.md`). Earlier sections below are as they were.

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

Partly done. The owner stopped it to start `docs/plans/PLAN_SECURE.md`. On Supabase and the default model, signed in as Dana Whitfield and Marcus Oyelaran. About $0.25 on OpenRouter in all (round 1's evaluations $0.13 and round 2's $0.09, with the improver's and judge's calls). This is an estimate from the round totals.

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

Built, from `docs/plans/PLAN_SECURE.md`: every route under `/api` with the agent's API key (`X-API-Key`, constant-time compare) on everything the agent's side calls; the agent reaches the backend only over HTTP (`agent/connection.py`, `api.py`, `improver_api.py`), keeping one database link, LangGraph's checkpoints; a second model (GPT-6 Luna at `none`, or `MODEL_NAME`) screens each claim before the claim agent's first model call, outside the versioned configuration, with CLM-1901 seeded into the queue (26 claims); `GET /api/agent/openapi.json` with the eight operations; a payment limit checked first (403 naming it); a sign-off that pays in the backend; `npm run build`, `backend.main` (`app` and a Mangum `handler`), the dashboard served at `/`, `NullPool` and schema-qualified statements on Lambda, and the `server` dependency group with its check.

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
- A sign-off or a send back the agent never heard of (the call failed or timed out) stands, but the claim's thread stays paused, and a signed-off claim is not saved to memory. A sent-back claim is worked by the next dispatch; a signed-off one is not resumed by anything, since nothing is left to decide.

## Not built

Real payments, messages to customers, rejecting a held payout.

## The context layer, memory and the complete record (2026-11-10)

Built in five phases (`docs/plans/PLAN_CONTEXT.md`). Checks: `npm run check`'s steps run piecewise with `.env` moved away: backend 579 passed and 1 skipped, the server and context imports on their own groups, front end 146 tests with the type check and lint clean, end to end 14 passed (one run, `.env` in place). Live checks on Supabase and the default model, direct route, memory off, signed in as Dana Whitfield by a script (`e2e/live_security.mjs`), so the password never passed through a tool call: **about $0.07 on OpenRouter in all**, from the `model_calls` table.

| Check | Result |
|---|---|
| One `npm run seed`, then `npm run dev` with its four parts | 26 claims waiting; the context layer answers at 8006 |
| `uv run -m agent CLM-1005`, then `CLM-1006` | Approve $243.00 and approve $425.00, both paid, each with its calculation, "UnderCover Agent, configuration version 1" and the manual's version 2026.2 |
| A dispatch at N=25 on the rest of the queue | **Finished only on the fifth dispatch, after three defects found by the run (below).** In the end all 26 claims are decided or awaiting a sign-off (22 decided, 4 awaiting approval); CLM-1901 is referred by the security check |
| An evaluation of the evaluation set's five tiny claims (run 6) | Finished: decision accuracy 100%, accuracy 80%, grade 1.33, mistakes $228, straight-through 80%, $0.0023 and 1 min 6 s per claim |
| The claim pages of CLM-1005 and CLM-1006 | Each tool call, each lookup with its file and where it came from (`plans.yaml`, `policy_wording.md`, `claim_rules.yaml`, `claims_manual.md`), the Context used panel, and the decision with its calculation line by line, who decided and when, and the manual's version. Checked in a browser driven by a script, with screenshots, not through the Playwright MCP server, which would have needed the password in a tool call |
| What the runs spent | The two claims $0.0025; the first dispatch $0.040 (94 calls); the later dispatches and the evaluation the rest. 169 model calls, $0.0726 |

What the memory steps, the Gateway's names and the Gateway's refusals look like is covered by tests only: the live runs have no Gateway and memory off, as the brief says.

### Three defects the N=25 dispatch found

1. **A decision held two connections.** The manual's version was read inside the transaction that records a decision, so enough decisions at once held all of the backend's 4 connections and each waited for a fifth: requests hung until the pool's 600 s wait ran out, and the agent's 30 s timeout fired first. Fixed by reading the versions first; `test_an_operation_never_holds_a_connection_while_it_asks_for_another` fails without it (a pool of one).
2. **A request waited 30 s at most.** Up to 25 claim agents share the backend's 4 connections and are meant to wait for one rather than fail. The agent's client now waits 300 s.
3. **A timeout became a refusal.** A transport error in a tool call was shown to the model as "Error: timed out", as if the app had said no. It now goes up like any transport error and the claim carries on from its last saved step.

And one found on the way: saved progress that had ended with no decision on record made carrying on crash with a `TypeError`, so a claim stayed in progress for ever. It now fails the attempt, and the next one starts afresh.

The dispatch's page shows the *previous* dispatch's summary until the new one finishes, so `e2e/live_security.mjs dispatch` returns at once when an earlier run exists; its runs are followed in the database instead.

## Sending claims back and the agent on AgentCore Runtime (2026-11-12)

Built in four phases (`docs/plans/PLAN_RUNTIME.md`). Checks, with `.env` moved away: backend 703 passed and 1 skipped; the three import checks (the app on the `server` group, the context layer on `context`, and `runtime.main` from `runtime/` and `agent/` alone on `runtime`); front end 154 tests with the type check and lint clean; end to end 14 passed. Live checks on Supabase and the default model, direct route, memory off, signed in as Dana Whitfield by a script (`e2e/live_security.mjs`), so the password never passed through a tool call: **about $0.063 on OpenRouter in all**, from the `model_calls` table.

| Check | Result |
|---|---|
| One `npm run seed`, then `npm run dev` with the agent on 8080 | 26 claims waiting; the four parts up |
| A dispatch at N=25 on the whole queue | Finished in 8 min 20 s: 14 approved, 5 declined, 7 referred; CLM-1003, 1008, 1011 and 1021 waiting for a sign-off. No timeouts, no stuck claim |
| A second start while it ran | The dashboard's Start dispatch button was off, and a request past it got **409 "a dispatch is already running"** |
| CLM-1003 ($3,750) sent back with a note | The agent took it once, read the note as Dana's, and decided again: it first tried to record without a new calculation and the app **refused it under claims manual section 14**, it calculated again and recorded $3,750 with a new reason, and the claim paused for sign-off again. The page shows both decisions, the note, who and when, and the refusal in red |
| CLM-1011 ($1,500) signed off | Paid, "signed off by Dana Whitfield"; the agent resumed it (7 s) and its work ended with one paid step |
| An evaluation of the evaluation set's five tiny claims | Finished: decision accuracy 100%, accuracy 80%, grade 1.56, mistakes $228, straight-through 80%, $0.0016 and 59.9 s per claim |
| `{"operation": "health"}` through `runtime/main.py` with `npm run dev` stopped | `{"healthy": true}` (the agent opened its checkpoint pool on Supabase to answer) |
| What the runs spent | The dispatch $0.053 (129 calls), the evaluation $0.008 (21 calls), the resume of CLM-1003 and the rest $0.001. 154 calls, $0.0628 |

The claim pages were read back through a browser driven by a script, with screenshots, not through the Playwright MCP server, which would have needed the password in a tool call.

### What did not go as the platform team wrote it

- **The deployment package cannot package our agent as `agentcore/cdk/lib/cdk-stack.ts` is written.** It stages `agent/` from the repository root and runs `uv export --only-group runtime` there; ours are `backend/agent/` and `backend/pyproject.toml`. The stack needs to stage `backend/agent` and run the export in `backend`, or the Python root has to move. Nothing here deploys, so nothing is changed in `agentcore/` or `runtime/`.
- **The `runtime` group needed `langgraph-checkpoint-sqlite`.** The agent's checkpoint module imports it at the top, and Runtime never uses it: the check found this when it imported the agent with only the group installed.
- **`agentcore/cdk` stages `runtime/`'s files at the folder's top, so the entry point there is `main`, not `runtime.main`.** The check stages them beside `agent/` instead, which imports the same.

### Known limits of this change

- A sign-off or a send back the agent never heard of stands, but the claim's thread stays paused; a signed-off claim is not saved to memory, and a sent-back one waits for the next dispatch, which resumes it with the note.
- The send back's resume and an old claim's saved progress: if a thread has ended with no decision on record, carrying on it fails the attempt instead of crashing; the next attempt starts afresh.

## The Copilot (2026-11-15)

### What works

- **The Copilot**, the handlers' assistant, in a sidebar on every signed-in page (`backend/agent/copilot/`, `frontend/src/copilot/`). A LangGraph graph written by hand (recall, model, `ToolNode`, confirm, save), checkpointed per conversation, an AgentCore app of its own (`agent/copilot/entrypoint.py`, `runtime/copilot.py`) served on 127.0.0.1:8081 by `npm run dev` and as the second runtime `copilot` in `agentcore/`.
- **It acts as the handler.** Every call it makes carries the handler's sign-in and never the agent's key; the app refuses any Copilot request that carries the key; the sign-in is in no checkpoint, step, memory write or log line (tests look for it, and found one leak in a log line, fixed).
- **It asks before it pays.** The sign-off pauses in a node of its own; the card is built by the app from its records; a freshly built Copilot resumes the same saved state and pays once.
- **It waits in code** (every 3 s, 15 minutes at most) and **the success criteria are worked out in code** from the evaluation's measures, the accuracy objective and the app's count of payouts over the limit (`GET /api/payouts/over-limit`).
- **Conversations are kept** in three tables (`copilot_conversations`, `copilot_turns`, `copilot_steps`), one turn at a time, a turn working 20 minutes shown as stopped, a waiting turn never stopped. `npm run seed` drops them.
- **Memory per handler** with `AgentCoreMemoryStore` (`("handlers", <id>)`), on only when `UNDERCOVER_COPILOT_MEMORY_ID` is set.
- The three success criteria are in `docs/specs/SPEC.md` section 15; `TECH_STACK.md`, `CODE_STANDARDS.md` and `PROCESS.md` describe the Copilot.

### Checks (2026-11-15)

- Earlier runs, kept for the record: the backend suite 738 passed, the isolated imports one by one, front end 180, end to end 19 of 20 before the Marcus fix.
- **`npm run check` as one command passed in a clean `git worktree`** (no `.env`, `node_modules` or `.venv`; `uv sync --all-groups --all-extras`, `npm ci` in `frontend/` and `e2e/`), on the commit that holds the review fixes: backend 813 passed and 1 skipped, the four isolated imports (server, runtime, the Copilot's health check through `runtime/copilot.py`, context layer), front end 183, type check, lint, and end to end 20 of 20. An earlier run in the working tree, before the review fixes, passed too (backend 807, front end 181, end to end 20 of 20).

### Live checks (2026-11-15, after one `npm run seed`, `npm run dev` on the default model, direct route, memory off)

Signed in as Dana Whitfield through `e2e/live_copilot.mjs` (the password is read from `.env` in that process only), in one conversation:

1. "Work the queue with 10 agents and tell me what needs me." The Copilot started the dispatch, waited for it in code (520 s, one `wait_for_run` card) and answered: 15 approved, 5 declined, 6 referred; four payouts held for sign-off (CLM-1003, 1008, 1011, 1021) and six claims referred.
2. "Why does CLM-1003 need me?" Answered from `get_claim`: a $3,750 payout over the limit, the agent's reason.
3. "Send it back with a note…" The Copilot sent CLM-1003 back with the note; the claim went back to the agent.
4. "Sign off on another one." It listed the held payouts, picked CLM-1008, paused, and showed the card ($2,240.00, Dorothy Gale, the agent's reason). Pressing Sign off and pay paid it: the payment is paid and signed off by Dana Whitfield.
5. "Are we meeting the success criteria we agreed?" It evaluated the whole held-out set (25 claims, 474 s) and answered: straight through 84.0% (target 70%), accuracy 84.0% (target 60%, the objective in Settings), over the limit 1 of 1 signed off: all three met. **Every figure matches the records**: `e2e/live_copilot_figures.mjs` read the evaluation's measures (straight_through 0.84, accuracy 0.84), the settings (objective 0.6) and the claims' payments (CLM-1008, paid, signed off by Dana Whitfield, the only claim paid over the limit).

- After `npm run dev` was stopped and started again, the conversation opened from its saved state (5 turns) and the Copilot answered a follow-up about it correctly (signed off CLM-1008, sent back CLM-1003).
- Signed in as Marcus Oyelaran, "Sign off CLM-1011" was refused in code ("only a handler who can sign off payouts over the limit can sign off a payout"), with no confirmation card.
- The sidebar in light and dark mode was checked from screenshots (`e2e/shots/copilot/`, not committed), which found one defect, fixed: a table in an answer was wider than the sidebar and was cut off; it now wraps.
- **What the runs spent** (OpenRouter): the dispatch $0.0604, the evaluation $0.0493 and its judge $0.0008, and the Copilot's own turns about $0.003 (a turn is 2 to 4 calls of $0.0002 to $0.0007). About $0.11 in all. Nothing was created on AWS.

### The independent review (2026-11-15)

A fresh reviewer (Opus, read only) went through the Copilot's code. It found no way for the sign-in to leak or for one handler to reach another's conversation. It found two Important and four Minor defects; all six are fixed, each with a test that failed first (`backend/tests/test_copilot_review.py`, `Markdown.test.tsx`, `Sidebar.test.tsx`):

- **Important: a long turn that is still alive could be shown as stopped** after 20 minutes, and a second run could then start on the same conversation. The 20 minutes now count from the Copilot's last word (a step begun or finished, or the handler's answer).
- **Important: an old confirmation card could pay a different amount** than it showed, if the claim had been sent back and held again. A sign-off from a card now needs the same payout (same payment, same amount) to still be held; otherwise it is refused with a 409 and the handler can cancel the card.
- Minor, fixed: a backend that is down when the Copilot checks the sign-in now fails the turn with a message, instead of leaving it working for 20 minutes (a refused sign-in still ends quietly); an image written by the model is no longer loaded by the browser; a long first argument no longer makes a card's input too long for the app; after a failed send (a 504) the sidebar reads the conversation again.
- Left as they are: the over-limit count compares payouts with the limit in force now (as the plan specifies, so a changed limit can show criterion 3 as not met for payouts that followed the rules of their day); the Approvals page does not poll; a Copilot on Runtime with no `UNDERCOVER_GATEWAY_URL` falls back to the direct context address and fails its first turn.

### What did not go as the course's notebook wrote it

- The Playwright MCP server did not connect (a timeout), so the sidebar was checked with a script that signs in as the handler and takes screenshots, as the last three changes did.
- Memory and the Gateway route were not exercised live: the spec asks for the direct route with memory off here, and the deployment notebook checks the rest on the deployed product.

### Known limits of this change

- **Nothing was deployed** (this change creates nothing on AWS; the deployment notebook does). The second runtime is in `agentcore/agentcore.json` and the stack settings and policy are changed (`UNDERCOVER_COPILOT_MEMORY_ID`, the Copilot's Memory in `runtime/policy.json`), but `agentcore deploy` was not run. The uncommitted work before this change restored `agentcore/agentcore.json`'s `agent` runtime and its three credentials, which a CLI step had emptied; check that file before a deploy.
- The Approvals page does not poll, so a payout the Copilot signs off shows there on the next visit (the nav's count updates at once).
- A sign-in that expires partway through a turn leaves the turn working until it shows as stopped after 20 minutes; the dashboard sends a sign-in with at least 20 minutes left, so this takes a turn longer than that.
- The live database holds this run's state: CLM-1008 paid, CLM-1003 sent back (the agent's rework was not followed up), other payouts held, and the evaluation of the whole held-out set.

## Reorganizing the repository (2026-11-16)

The course is finished, so the repository was reorganized to be read from outside, as a portfolio, and to be mined for ideas by the next project. The plan is `docs/plans/PLAN_REORG.md`. A light reorganization: the code stays where it was, so the build, the tests and the deploy read the same paths.

- **Labs** are in `labs/weekN/dayN/`, each notebook with the paths it reads fixed, and `labs/README.md` says what each teaches and where it ended up. The Week 3 Day 4 lab project (`undercoverlab`, which was a repository of its own inside this one) is a folder in `labs/week3/day4/`.
- **Docs** are split into `docs/specs/` (what and why) and `docs/plans/` (how), with `docs/README.md` reading them in the order the project was built, and `docs/DECISIONS.md` for the choices and their reasons.
- **The root** holds only the project. `lab1_desk/`, `lab2_desk/` and `monitor.py` went to the labs; the generated folders (`build/`, `dashboard/`, local databases, caches) were deleted, and the AgentCore CLI's cache and state are ignored.
- **No account data in any file.** `scripts/scrub_notebooks.py` replaces the account id, the Supabase, Lambda and CloudFront hosts, keys and the work e-mail with examples in the labs. `backend/tests/test_repo_layout.py` fails if any file git would track holds one, and also pins the layout: where the labs are, that every path a lab reads exists, that every doc path the repository names exists, and what the root may hold.
- **The README** is rewritten for a reader from outside, with a map of "to see how I did X, open Y". Setup, the tests and reseeding moved to `COMMANDS.md`.
