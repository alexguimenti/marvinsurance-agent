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

## Live verification

Not done yet: it needs the owner (see Task 11 of `docs/PLAN_SWARM.md`). It spends on OpenRouter and writes to Supabase and Langfuse.

## Known limits

- Supabase is far from where this runs: about 220 ms a trip and 6.5 s for a new connection, so connections open at startup. With 25 claim agents sharing a process's 4 connections, they queue for them; how much time that costs is to be measured live.
- A second attempt at a claim has a new thread, so a new Langfuse session. The claim id tag finds them all.
- After a reseed, thread names repeat, so an old Langfuse session gains new traces.
- `AGENT_MODEL` is still read, from the field's name, but `MODEL_NAME` wins over it.
- Stopping `npm run dev` with Ctrl-C was not exercised by a real keypress; stopping when one part dies was.

## Not built

Sign-in, real payments, messages to customers, rejecting a held payout.
