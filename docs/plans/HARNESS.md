# HARNESS: letting the Marvin Agent organize its own work

Status: draft, to be agreed before `docs/plans/PLAN_HARNESS.md` is written. Items marked **Proposed** are my choices where the brief is silent. Change them freely.

Source: the head of claims operations' brief "Getting ready for harder claims". This file turns it into requirements we can plan, build and test against. Where it conflicts with `docs/SPEC.md`, this file wins once agreed, and `SPEC.md` is updated (section 11).

## 1. Why

Claims will get harder: more documents, more rules, more to look up. The pilot works because our code organizes the agent's work:

| In the pilot | Where we are heading |
|---|---|
| Code loops over the claims and feeds the agent one at a time (`runner.run_queue`) | The agent starts once, plans the queue, picks the order and works every waiting claim |
| The agent gets the whole policy wording on every claim (`get_policy_wording`) | The agent finds and reads only the sections of the wording and the manual that it needs |
| The claims manual is out of scope | Decisions follow the manual as well as the wording |
| A failing claim is retried in place, then becomes a `refer` | A failed claim goes back in the queue; failing twice leaves it for a handler |
| The team sees one claim's trace at a time | The team also sees the agent's to-do list for the whole run |

"Harness" here means the code around the model: the run loop, the to-do list, the subagents, the lookup tools, the recovery rules and the limits. It stays our own code on LangChain. We add **no** other agent framework.

## 2. Differences between the brief and this repo

The brief was written against a different checkout. Settle these before planning.

| The brief says | This repo has | Proposed |
|---|---|---|
| "UnderCover Agent" | MarvInsurance and the Marvin Agent | Keep our names |
| `docs/STATUS.md` | Does not exist | Create it, or keep `README.md` as the status page. Pick one (D8) |
| `docs/PROCESS.md`, `docs/CODE_STANDARDS.md` | Do not exist | Follow the workflow we used in the pilot (spec, plan, TDD, tick off the plan) and keep the code simple. Say if you have the originals |
| `npm run check` | No root `package.json` | Add one command that runs everything (D8) |
| `MODEL_NAME` overrides the model | `AGENT_MODEL` env var, then `agent.toml` | Keep `AGENT_MODEL` (D7) |
| A claim that fails partway "stays stuck in progress" | Crashed `in_progress` claims already reset to waiting at server start; failed attempts already retry in place | Replace with re-queue (R5) |
| The pilot "feeds the agent one claim at a time" | True | R1 and R3 |

## 3. What does not change

- Every rule in `docs/SPEC.md`, including sign-off on payouts over **$1,000**, enforced by the system and not by the agent (`payments.request_payment`).
- `data/evals/` and `data/security/` are never read by the agent, its tools, its prompts, the screens or any unit test. Only `scripts/score.py` reads `data/evals/`. The existing isolation test keeps passing and grows to cover the new files.
- Customer text is data, not instructions. `<claim_data>` wrapping and the CLM-1901 test stay.
- Stack: FastAPI, SQLite, LangChain with OpenRouter, React.
- Tests never call a model. Unit tests use a stub model. e2e tests use the scripted agent.
- No sign-in, no real payments, no messages to customers (so manual section 12, "Writing to the customer", is not applied).
- Amounts in USD.

## 4. Requirements

Each requirement has behavior, then what must be tested. Numbers match the brief.

### R1. One run works the whole queue

- A **run** is one start of the orchestrator agent. It plans, works every waiting claim in the order it chooses, and ends with a short summary for the handlers.
- Start a run from the queue page ("Run queue") and through the API.
- **Cap.** A run can be capped at N claims. The agent takes at most N waiting claims. The rest stay `waiting`, untouched. No cap means all waiting claims.
- **One named claim.** Running one claim ("Run this claim", or `claim_ids`) still works. It is a run with one claim in the plan.
- **Selected claims.** The pilot's checkboxes keep working: "Run selected (N)" starts a run limited to those ids.
- **Proposed:** only one run at a time. Starting a second returns 409. Claims within a run are worked one after another, not in parallel, to keep cost and order easy to follow.
- **Summary.** The run ends with a few lines for handlers: counts by outcome, the claims awaiting approval with amounts, claims left for a handler and why, and anything odd. Stored on the run and shown on the queue page.
- **Order.** The agent chooses it. Our code does not sort for it. (A reasonable agent might do held-for-approval-prone or simple claims first. We do not prescribe it.)

Tests: cap respected and the rest left waiting; no cap works all; one named claim; second run while one is active is refused; summary stored; the run ends `finished` or `failed`, never left `running` after a crash (R5).

### R2. A to-do list we can watch

- The orchestrator plans as a to-do list and keeps it up to date as it goes.
- Item statuses: `todo`, `in_progress`, `done`, `left_for_handler`.
- **Proposed** shape: one item per claim, plus optional free items such as "Write the summary". Each item has a position, a title, a status and an optional `claim_id` and note.
- The agent edits the list through two tools: `set_todo(items)` replaces the plan, and `update_todo(item_id, status, note?)` changes one item. The items are stored in the database, not only in the model's context, so the list survives a crash and can be shown.
- Our code, not the agent's good manners, guarantees a claim item only turns `done` when its claim is decided. See R5.
- The **queue page** shows the list for the current run (or the latest finished run) and refreshes on the same 2 second poll. A viewer sees what the agent means to do and how far it has got.
- Each change is also a trace event on the run, so the history is kept.

Tests: list stored; `update_todo` rejects unknown ids and unknown statuses; list endpoint returns the current run's items in order; the queue page renders each status and updates when an item changes; an item cannot be `done` for an undecided claim.

### R3. A fresh helper for each claim

- The orchestrator hands each claim to a **subagent** through one tool: `work_claim(claim_id)`.
- The subagent starts from a clean context, with no memory of the other claims or of the plan. It gets the claim-working instructions (R7) and the claim's own tools. It works that one claim and finishes with a decision as in the pilot (`Decision`, with calculation, rationale and citations).
- It reports back to the orchestrator in **one line**: the decision and payout (`approve $420.00`, `decline`, `refer: missing police report`), or what went wrong. The orchestrator never receives the subagent's full transcript. This keeps the orchestrator's context small, so each step stays cheap as the run grows.
- Every step the subagent takes is recorded **on the claim** (trace events with `claim_id`), as in the pilot. The run's own events (plan, to-do changes, hand-offs, summary) are recorded on the run.
- Implementation: a new LangChain `create_agent` per claim, built by the same `build_agent` seam, so the scripted agent can stand in for both roles. No new framework.
- The subagent can only reach its own claim. Tools stay bound to one `claim_id` as today. The $1,000 limit is unchanged and applies in `payments.request_payment`.
- **Cost.** Usage and cost are recorded per claim (as now) and per run (sum of the orchestrator and all subagents). The queue page shows the run cost.

Tests (stub model): a subagent for each claim starts with empty history; the orchestrator sees only the one-line report; the claim's trace holds the subagent's steps; a subagent cannot read another claim; the run cost equals the sum of its parts; over-limit approvals are still held.

### R4. The claims manual, looked up like a handler would

- The agent no longer gets whole documents. It looks sections up and reads them.
- **Documents:** `policy_wording` (`data/policy_wording.md`, sections 1 to 12) and `claims_manual` (`data/claims_manual.md`, sections 1 to 14). `policy_wording_short.md` is not used.
- **Tools** (read-only, replacing `get_policy_wording`):
  - `list_sections(document)` returns each section's number and title.
  - `read_section(document, section)` returns that section's text.
  - **Proposed:** `search_documents(query)` returns matching section numbers with a short snippet, so the agent can find "wild animals" or "meals without receipts" without knowing where they live. Plain keyword matching, no embeddings.
- Sections are cut from the Markdown `##` headings. The loader lives next to `Reference` and is a whitelist: only these two files.
- **Record what was read.** Every lookup is a trace event naming the document and section, not the text. The **claim page** gets a "What the agent read" list: each document and section, in order, de-duplicated. Anyone can see whether a decision rested on the manual.
- **Manual version.** Manual section 13 and 14 require the manual's version on every decision. Store `manual_version` (`2026.2`, read from the manual's first lines) and the wording version on the decision, and show them on the claim page.
- **Instructions must send the agent to the manual.** The claim instructions say to follow the manual's working order (manual section 4), to check the customer's claim history before deciding (manual section 9), and to read the sections that apply rather than guess.
- The manual changes some decisions the pilot made from the wording alone: third claim in 12 months goes to `refer`, wild animals are covered as accidental loss, meals without receipts pay a flat $35 per traveler per 24 hours, pre-existing conditions look back 60 days, a missing document is a `refer` and not a decline. Check each of these against the data once the agent runs.
- Update `docs/SPEC.md`: section 3.1 inputs, section 8 "Out of scope" (remove the manual), section 10 "Open questions".

Tests: the section loader splits both documents into the right number of sections and titles; `read_section` on an unknown section returns a clear error, not an exception; `search_documents` finds "wild animals" in manual section 7; lookups are traced with document and section but not the text; the claim endpoint returns the "read" list; no tool can open any other file; `data/evals` strings are still absent from `backend/app` and `frontend/src`.

### R5. Problems are put right automatically

A long run meets timeouts, provider errors and failing steps. The rule: **one bad claim never stops the run and never leaves a claim stuck.**

Failure kinds and what happens:

| What fails | What happens |
|---|---|
| A single model call (timeout, 429, 5xx) | Retried a few times with a short backoff inside the step. This is not a claim failure yet. **Proposed** setting `step_retries`, default 2 |
| A subagent ends with no decision, raises, runs out of steps, or runs past the claim time limit | The attempt failed (below) |
| The agent asks to pay an amount that differs from its decision | Attempt failed, as in the pilot |
| The orchestrator itself fails | The run is marked `failed`. Claims it left `in_progress` go back to `waiting`. Nothing is half-decided |
| The server stops mid-run | At start, any `running` run becomes `failed` and any `in_progress` claim goes back to `waiting` (today's recovery, extended to runs) |

**Attempts.** Each claim carries an `attempts` count.

1. First failure: the claim's partial work is undone (its payment and partial decision are deleted, as today), `attempts` becomes 1, the claim goes **back to `waiting`**, and the to-do item goes back to `todo`. The run carries on with the others and tries it again later in the same run.
2. Second failure: the claim is **left for a handler**. The to-do item is `left_for_handler` with the reason. The run carries on.

The brief says "fails twice". So the pilot's `max_attempts = 3` retry-in-place loop is replaced: the default becomes 2 attempts and each is a full fresh subagent run. (D2)

**Left for a handler.** **Proposed:** a new claim status `needs_handler`, shown in the queue as "Needs a handler", with the reason from the last error. It is not a `refer` decision, because the agent never made one: the pilot's automatic `refer` made a failure look like a judgment. (D1)

**Never decided or paid twice.**
- A claim starts work only through an atomic `UPDATE ... SET status='in_progress' WHERE status='waiting'`. If that changes no row, the claim is not started. Two runs or two clicks cannot both take it.
- `decisions.claim_id` and `payments.claim_id` are unique. A second write is refused by the database, not by the agent.
- The attempt cleanup (above) is the only place a decision or payment is deleted, and only for a claim that did not finish.
- A claim that is `decided`, `awaiting_approval` or `needs_handler` cannot be started again by a run. Re-running needs an explicit reset.

**Check for missed claims.** Before it finishes, the orchestrator calls `finish_run(summary)`. The tool compares the plan with the database: if any claim within the cap is still `waiting` and has not been tried, or is `in_progress`, the call is **refused** with the list, and the agent keeps working. Our code runs the same check again when the run ends and, if the agent could not satisfy it, marks the run `failed` with the missed ids and puts any `in_progress` claim back to `waiting`.

**Limits** so a run cannot spin (**Proposed** settings in `agent.toml`):
- `max_steps` for a subagent and for the orchestrator.
- `claim_timeout_s`: wall-clock limit for one claim attempt.
- No run-level budget in this phase. See open questions.

Tests (stub model, no network): one failing claim among five leaves the other four decided and the run `finished`; a claim failing once goes back to `waiting`, is retried in the same run and gets decided; a claim failing twice becomes `needs_handler` with its to-do item `left_for_handler`; partial payment from a failed attempt is deleted; a server restart mid-run recovers the run and the claims; two simultaneous starts of one claim work it once; a second write of a decision or a payment is refused by the database; `finish_run` is refused while a claim within the cap is untried and accepted after; a failed orchestrator never leaves `in_progress` claims behind.

### R6. The model

- Default model: `deepseek/deepseek-v4.1-flash` on OpenRouter, reasoning effort `low`. Set in `agent.toml`, which replaces today's `openai/gpt-6-luna` and `high`.
- Override with the `AGENT_MODEL` environment variable (the brief calls it `MODEL_NAME`; D7). Precedence stays: constructor, then env, then `.env`, then `agent.toml`.
- The orchestrator and the subagents use the same model and effort. **Proposed:** no separate setting until a trial shows it matters.
- OpenRouter model ids ending in `:batch` do not work on the chat endpoint (seen in the pilot).
- Update README, `COMMANDS.md`, `docs/TECH_STACK.md`.

Test: settings load the new defaults from `agent.toml` and the env override wins.

### R7. Instructions we can read

- Two instruction files, plain Markdown in `backend/app/`:
  - `instructions_run.md`: how to run the queue (plan as a to-do list, choose the order, hand each claim to a helper, keep the list current, handle the cap, recover from failures, check for missed claims, write the summary).
  - `instructions_claim.md`: how to work one claim (from today's `instructions.md`, extended with the manual's working order, repeat-claim check, document requests and look-up habits).
- Written from the policy wording and the manual. **Never** from `data/evals/`, and no claim from the queue or the held-out set is quoted in them.
- The instructions page shows both, each with a heading and a short intro. `GET /instructions` returns both.
- The page shows what the agent runs with now. Reminder from the pilot: `--reload` does not watch `instructions*.md`, so a restart is needed after editing them.

Tests: the endpoint returns both texts; the page renders both; the isolation test scans both files for `evals` strings and for claim ids.

## 5. Data model changes (Proposed)

New or changed tables and columns. Existing ones stay.

| Table | Change |
|---|---|
| `runs` (new) | `run_id`, `status` (`running`, `finished`, `failed`), `cap`, `started_at`, `finished_at`, `summary`, `error`, `model`, usage and cost totals |
| `todo_items` (new) | `item_id`, `run_id`, `position`, `title`, `status`, `claim_id` (nullable), `note` |
| `trace_events` | add nullable `run_id`; `claim_id` becomes nullable so run-level events fit. A claim event keeps its `claim_id` |
| `claims` | add `attempts` (default 0), `last_error`; new status value `needs_handler` |
| `decisions` | add `manual_version`, `wording_version`; `claim_id` unique (check it already is) |
| `payments` | `claim_id` unique (already) |

`reset_analyses` clears the new tables too, so `scripts/reset_db.py` stays the "reseed" command.

## 6. API changes (Proposed)

| Route | Change |
|---|---|
| `POST /runs` | Body `{claim_ids?, limit?}`. Starts a run, returns `run_id`. 409 if a run is active. Replaces `POST /queue/run` and `POST /claims/{id}/run` (kept as thin wrappers over it for the existing buttons, then removed if nothing uses them) |
| `GET /runs/latest` | The current or latest run with summary, cost and to-do items |
| `GET /runs/{id}` | A run by id, with its trace |
| `GET /claims/{id}` | Add `read` (documents and sections the agent read), `attempts`, `last_error`, versions |
| `GET /instructions` | Returns both instruction texts |

## 7. Front end changes

- **Queue page:** the to-do list panel for the current run, the run's status, cap, cost and summary, a numeric "limit" field next to "Run queue", a `Needs a handler` status tile and badge.
- **Claim page:** "What the agent read", versions, attempts and last error when relevant. The activity panel keeps its summaries and shows the subagent's steps as now.
- **Instructions page:** both documents.
- Style follows `docs/DESIGN.md`.

## 8. Tests

The brief's bar: `npm run check` green with a test on a stub model for each new behavior. Ours:

- **Unit (pytest, Vitest):** one or more tests per bullet under "Tests" in R1 to R7.
- **Stub model:** a deterministic fake that can play both roles and can be told to fail on a claim, fail once, fail twice, time out and skip a claim. `ScriptedAgent` and `FakeToolModel` are the starting points.
- **e2e (Playwright, scripted agent, own ports 8100 and 5273):** start a run capped at 3 and watch the to-do list fill and tick off; the other claims stay waiting; a claim that fails twice shows as "Needs a handler"; the claim page lists what was read; the instructions page shows both documents; held-for-approval still works.
- **Real model:** never in the test suites. The OpenRouter smoke test stays skipped without a key.

## 9. Live runs with the real model

- Reseed first: `scripts/reset_db.py`.
- Cap every run at **5** claims.
- Use the default model. Report the cost from the run's totals.
- Then, once, run `scripts/score.py` to compare with the pilot (queue: gpt-4o 60% decision, 52% payout). With the manual in play, `manual`-difficulty claims should now be possible. I have not looked at the evals and will not.

Done looks like: after reseeding, a 5-claim run on the default model works five claims on its own; the dashboard shows the list filling and ticking off, and each claim's steps with the sections read; the other 20 stay waiting.

## 10. Documents to update

- `docs/SPEC.md`: sections 3 (inputs, behavior), 5 (new status), 8 (manual no longer out of scope), 9 (failures, trigger), 10.
- `docs/TECH_STACK.md`: orchestrator and subagents on LangChain, run and to-do storage, default model.
- `docs/STATUS.md` or `README.md`: what the product is now (D8).
- `README.md`, `COMMANDS.md`: model default, new commands, `agent.toml` settings.
- `data/README.md`: the manual is now used by the agent.

## 11. Decisions to settle

Each has my recommendation.

| # | Question | Recommendation |
|---|---|---|
| D1 | What is a claim "left for a handler"? | New status `needs_handler`, no decision recorded. The pilot's automatic `refer` stays only for decisions the agent really makes |
| D2 | Attempts per claim | 2, each a full fresh run, with the failed claim going back in the queue (not retried in place) |
| D3 | Does a retry count against the cap? | No. A claim keeps its slot and its to-do item |
| D4 | Parallel claims? | No. Sequential, one run at a time |
| D5 | Section lookup by headings only, or also search? | Headings plus plain keyword `search_documents` |
| D6 | Orchestrator and subagent models | The same one |
| D7 | `MODEL_NAME` or `AGENT_MODEL`? | Keep `AGENT_MODEL`. Add `MODEL_NAME` as an alias only if you need it |
| D8 | One `check` command and a status page | Add a root script that runs pytest, Vitest, `tsc` and Playwright. Keep status in `README.md` instead of a new `STATUS.md`, unless you want both |

## 12. Open questions

- Should the team be able to stop a run midway? The brief does not ask. Proposed: not in this phase.
- A cost ceiling per run? The cap on claims limits cost today. Proposed: not in this phase.
- Should an approved-but-held payment count as "done" in the to-do list? Proposed: yes, `done` means decided by the agent. The approval is tracked on the Approvals page.
- Once a claim is `needs_handler`, how does a person put it back? Proposed: out of scope, as with referrals in the pilot.

## 13. Suggested build order

For `docs/plans/PLAN_HARNESS.md`, smallest working step first, each with its tests:

1. Section loader and the three lookup tools; trace and "What the agent read" (R4).
2. `runs` and `todo_items` tables, run API and the stub model that plays both roles (R1, R2 storage).
3. Claim subagent behind `work_claim`, with one-line reports and per-claim traces (R3).
4. Orchestrator loop, to-do tools, cap, `finish_run` check, summary (R1, R2).
5. Recovery: attempts, re-queue, `needs_handler`, atomic start, unique writes, restart recovery (R5).
6. Two instruction files and the page (R7).
7. Default model and settings (R6).
8. Queue page to-do panel, claim page changes, e2e (R2, R4, R5).
9. Docs, one-command check, live 5-claim run, score.




