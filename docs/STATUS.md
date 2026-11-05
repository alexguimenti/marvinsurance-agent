# Status

Last updated 2026-11-05, after the swarm and the scoreboard (`docs/PLAN_SWARM.md`).

## What works

- The queue, claim detail, the agent's live activity, its instructions, approvals and the Scoreboard, on `npm run dev`.
- **Dispatches.** A dispatcher with no model works every claim left in progress and every waiting claim, oldest submitted first, with N claim agents at once (1 to 25, 3 to start with). The run panel shows each claim and where it stands, the dispatch's model cost and agent time, and, when it ends, a summary from the records.
- **Problems are put right.** A rate limit or a timeout waits and carries the claim on from its last step, as the same attempt; still failing after its waits, it stays in progress for the next dispatch. Any other error puts the claim back in the queue and the same dispatch tries it once more; a claim that fails twice is left for a handler. One bad claim never stops a dispatch.
- **One run at a time.** A second start of a dispatch or an evaluation is refused. When the agent's service starts, a run left running shows as stopped, and the next dispatch carries on its claims.
- **Cost and time.** Every model call's tokens and cost, and every stretch of agent time, are recorded with their dispatch or evaluation, or with no run. A wait for a sign-off is not counted.
- **Evaluations.** On copies of the evaluation set (the queue's 25 claims) or the held-out set, whole or their five tiny claims. Copies never show in the queue, are never carried on by a dispatch and cannot be signed off; a held approval counts as finished, and a finished copy's progress is cleared. The Scoreboard lists them with decision accuracy, accuracy, the cost of mistakes, straight-through rate, cost and time per claim, and the time for the run, and opens each to its claims beside the handlers'.
- **Sign-offs** still pay within seconds through the service, and nothing is ever decided or paid twice.
- **Langfuse:** each claim keeps its session per thread, and each trace carries the tag `run-<id>` of its dispatch or evaluation.
- Default model: DeepSeek V4.1 Flash with low reasoning effort (the owner kept it in place of GPT-6 Luna). `MODEL_NAME` chooses another, with no effort.

## Checks

`npm run check` in a clean worktree with no `.env`: 272 backend tests (1 skipped), 66 front-end tests, the type check, the lint and 9 end-to-end tests, all passing.

## Live verification (2026-11-05)

On Supabase and the default model, after one `npm run seed`, through the Playwright MCP server. $0.075 on OpenRouter in all.

| Check | Result |
|---|---|
| A dispatch at N=3 | Oldest submitted first, never more than 3 at once. Each claim took 14 to 26 s |
| Stopped partway with a forced kill of the process on 8002 | `npm run dev` stopped the backend and the dashboard too. After a restart the run showed as stopped. 8 claims were finished, 3 held, 2 in progress |
| A dispatch at N=25 then | The 2 in progress carried on as the same attempts (`CLM-1004-1`, `CLM-1023-1`, attempts 0), with one decision each. The other 15 waiting claims started with them: all 17 at once, started within 14.5 s of each other |
| Its run against the sum of its claims | 94 s for the run; 1,071 s of agent time summed over its claims |
| Each claim, against N=3 | **Slower: 32 to 84 s, against 14 to 26 s.** See below |
| A sign-off during the day | Paid 2.8 s after the click on the approvals page (signed off at 1.3 s), and "Paid ... signed off by Dana Whitfield" on the claim's page |
| No claim fails for want of a connection | None did. No pool timeout in the logs |
| At most 12 connections for user postgres | 10 at the most, polled every 8 to 13 s during the dispatch and the evaluations. Read with SQL from our own connection, not through the Supabase MCP server, which this session did not have |
| An evaluation of the evaluation set at N=25 | Finished in 128 s: decision accuracy 92%, accuracy 88%, mistakes $360, straight-through 84%, $0.0012 and 81 s per claim |
| The held-out set's five tiny claims at N=5 | Finished in 38 s: decision accuracy 100%, accuracy 80%, mistakes $70, straight-through 80%, $0.0016 and 25 s per claim |
| The queue's counts before and after the evaluations | The same: 23 decided, 2 awaiting approval |
| The N=25 dispatch in Langfuse by its tag | 17 traces tagged `run-2`, one for each of its claims |

### Where the time goes at N=25

Each claim does dozens of short statements: its trace events (302 in that run), one row per model call (58), the claim's own reads and writes, and a checkpoint after every step, with its writes. Every statement is a trip of about 213 ms to Supabase, and the process has 2 connections for records and 2 for checkpoints. With 17 claim agents at once, they spend much of their time waiting for one of those 4, not for the model. This is an estimate from the counts and the trip time, not a profile. Ways to win it back, keeping to 4 connections: fewer trace events per step, writing a step's events in one statement, or `durability="exit"` for steps that need no checkpoint.

## Known limits

- Supabase is far from where this runs: about 220 ms a trip and 6.5 s for a new connection, so connections open at startup. With many claim agents sharing a process's 4 connections, they queue for them: each claim took about three times as long at N=25 as at N=3 (see above).
- A second attempt at a claim has a new thread, so a new Langfuse session. The claim id tag finds them all.
- After a reseed, thread names repeat, so an old Langfuse session gains new traces.
- `AGENT_MODEL` is still read, from the field's name, but `MODEL_NAME` wins over it.
- Stopping `npm run dev` with Ctrl-C was not exercised by a real keypress; stopping when one part dies was.

## Not built

Sign-in, real payments, messages to customers, rejecting a held payout.
