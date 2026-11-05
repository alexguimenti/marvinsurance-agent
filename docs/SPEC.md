# SPEC: Marvin Agent for MarvInsurance

Status: pilot plus the harness (`docs/plans/HARNESS.md`). This is a course project. Nothing here is a real company, customer or payment.

## 1. Overview

MarvInsurance sells travel insurance in three plans: **Economy**, **Premium** and **First**. Today every claim is reviewed by hand. The analyst reads the customer's story and documents, checks the customer's history and the policy, applies the policy wording, and then does one of three things:

- **Approve**, with a payout.
- **Decline**.
- **Refer** to another person, when the claim needs more judgment or more information.

We are building an AI agent, the **Marvin Agent**, that works the claims queue the way a human analyst does. A dispatcher, which is code and no model, hands every claim to a claim agent of its own, up to N at once; each claim agent reads the claim, the policy, the customer's history and the sections of the policy wording and the claims manual it needs, makes a decision, and for an approval works out and prepares the payment. Staff get screens to watch and control the work, and a scoreboard of how often the agent agrees with the handlers, what a claim costs and how long it takes.

The goal is to handle the queue accurately and consistently, as a careful person would.

> Naming note: the seed data originally used the name "UnderCover Travel Insurance". It was renamed to MarvInsurance throughout `data/` (company name, plan names, handler email domains, policy wording, manual). The agent is the Marvin Agent.

## 2. Users

The team that runs the queue (claims handlers and their lead). There is no sign-in in the pilot: everyone who opens the screens sees everything.

## 3. The agent

### 3.1 Inputs

For each claim the agent reads:

| Input | Source |
|---|---|
| The claim: story, items, amounts, supporting documents (as text) | `data/claims.json` |
| The policy for the trip: plan, add-ons, purchase date, trip dates, travelers | `data/policies.json` |
| The customer's claim history (the agent does not see contact details) | `data/customers.json` |
| Public facts (events, products, places) for "publicly known before purchase" | `data/knowledge.json` |
| The plan limits, waiting periods, deductibles and add-on effects | `data/plans.json` |
| Company settings (approval limit, filing window, look-back) | `data/company.json` |
| The policy wording, looked up section by section | `data/policy_wording.md` |
| The claims manual, looked up section by section | `data/claims_manual.md` |

The agent is not handed whole documents. It gets the index of both documents' sections, finds sections with `search_documents` and reads them with `read_section` (at most 8 per claim), and the claim page lists what it read. `data/policy_wording_short.md` also exists and is not used.

### 3.2 Outputs

For every claim the agent produces:

1. A **decision**: `approve`, `decline` or `refer`.
2. For an approval, a **payout amount** and its calculation, following the payment steps in the policy wording, section 10 (eligible amount, minus third-party payments, minus deductible, capped at the benefit maximum).
3. A **rationale** of two or three short sentences that explains the decision and **names where in the policy wording it relies** (for example, "section 5, theft must be reported to police within 24 hours").

### 3.3 Behavior rules

- Decisions follow the policy wording first and the claims manual where the wording leaves things open (for example a third claim within 12 months, losses to wild animals, meals without receipts). The agent does not invent rules.
- Each decision records the manual and wording versions it was made under (manual sections 13 and 14).
- A claim that needs judgment the wording does not settle, or a document that is missing, is **referred**, not guessed.
- Customer-supplied text (stories, documents) is data, not instructions. The agent must not follow instructions found inside a claim. The case in `data/security/` (CLM-1901) tests this and must stay out of the queue and the evaluation sets.
- All amounts are in US dollars.

## 4. Payments and the approval limit

- The approval limit is **$1,000** (`payout_approval_limit` in `company.json`).
- Any payout **above $1,000** needs a person's manual approval. The agent may decide to approve such a claim, but the payment **waits** for a handler's sign-off.
- The limit is enforced by the **system**, not by the agent's good behavior. The payment step must block any payout over the limit until it is approved, even if the agent tries to pay it.
- Handlers who may approve over the limit are listed in `data/handlers.json` (`can_approve_over_limit`).
- A valid claim over the limit is still an **approve** decision. Only the payment is held.
- Payments are simulated (see section 8).

## 5. Claim lifecycle

Every claim has one status:

| Status | Meaning |
|---|---|
| Waiting | In the queue, not yet picked up |
| In progress | The agent is working on it, or has been stopped partway and will carry on, or a handler has signed off and the agent is paying |
| Awaiting approval | Decided and approved, but the payout is over $1,000. The agent has stopped and waits for a person |
| Decided | Finished: approved and paid (simulated), declined or referred |
| Needs a handler | The agent failed on it twice and made no decision. A person has to take it |

An evaluation's copies of claims have the same statuses, but they never show in the queue and nobody signs them off: an approval held for sign-off counts as finished.

## 6. Screens

The pilot needs these views. Everyone sees everything.

1. **Claims queue**: every claim with its current status; a choice of N, the claim agents at once (1 to 25, 3 to start with), and a button that starts a dispatch; and the run panel for the latest dispatch: each of its claims and where it stands, its model cost and its agent time, and, once it ends, a summary built from the records: how many were approved, declined and referred, each payout waiting for a sign-off, and each claim left for a handler or still in progress.
2. **Claim detail**: the full claim (story, items, documents, policy and customer context), the agent's decision, payout and rationale, the sections of the wording and the manual it read, and, for a claim left for a handler, how many attempts failed and why.
3. **Agent activity**: what the agent is doing on a claim, step by step, while it works (the trace of what it reads, checks and decides).
4. **Agent instructions**: the claim agent's instructions, so the team can see and review them. The dispatcher is code and has none.
5. **Approvals**: payments waiting for sign-off, with an approve action for authorized handlers. A handler can only sign off for now; rejecting a held payout comes later. The claim then shows who signed off and when.
6. **Scoreboard**: every evaluation, newest first, with its set, N, model and reasoning effort, and the measures from the team's labs: decision accuracy, accuracy (the decision, and a payout within a dollar), the cost of mistakes, the straight-through rate, model cost per claim, time per claim and the time for the whole run. A handler starts an evaluation here. Each opens to its claims, the handler's decision and payout beside the agent's, the misses marked, each linked to its page. A copy's page says it is a copy and has no sign-off.

Visual style follows `docs/DESIGN.md`.

## 7. Data and evaluation

All data lives in `data/` and is described in `data/README.md`: claims queue (25 claims), policies, customers, plans, handlers, company settings, policy wording and the claims manual.

**Evaluation data is off limits to the agent.**

- `data/evals/` holds the handlers' own decisions, plus held-out claims. They are used only to check the agent's work.
- Seeding stores the held-out claims, out of the queue, and the handlers' decisions, in tables of their own that no claim operation reads. The agent, its prompts and its tools never read them; the backend scores each evaluation from them.
- Two labelled sets: the **evaluation set**, the 25 claims of the queue, and the **held-out set**. Each has five `tiny` claims for a cheap check.
- An **evaluation** works its own copies of a set, whole or tiny, through the same dispatcher and claim agent as a dispatch. Each copy has a new id, such as `E7-CLM-1003`. The queue stays as it was.
- `data/security/` holds a prompt-injection test case. It is kept out of the queue.

Evaluation scores the decision and the payout of each claim. A claim the agent didn't decide counts as a referral. The cost of a mistake: a wrongful decline $1,500, a wrongful approval its payout (paid or not), a needless referral $50, a wrong amount on a right approval the difference. The straight-through rate is the share of claims not referred.

## 8. Out of scope for the pilot

- **Sign-in and access control.** Everyone who uses the screens sees everything.
- **Real payments.** Payments are simulated.
- **Messages to customers.** Manual section 12, "Writing to the customer", is not applied.
- **Stopping a run midway** and a cost ceiling per run. N and the tiny sets are the cost control.

## 9. Decisions made in the pilot

- **Trigger:** "Start dispatch" on the queue page starts a dispatch with N claim agents at once; "Start evaluation" on the Scoreboard starts an evaluation. Only one dispatch or evaluation runs at a time, and a second start is refused. The backend passes N and the set to the agent's service, which answers at once and works in the background; the dashboard says so if the service is not running. `uv run -m agent CLM-1001` works one claim. Hosting is local only.
- **Dispatches:** a dispatch works every claim left in progress and every waiting claim, oldest submitted first, with up to N claim agents at once. Code chooses the claims and keeps track of the run, with no model. Each claim keeps its own thread. A claim the service is resuming after a sign-off is left to it. The run's summary is built from the records.
- **Paying:** paying is a step of the claim agent's work for every approval, straight after the decision when no sign-off is needed. When the backend says a sign-off is needed, the agent stops and waits, keeping everything it has worked out. As soon as a handler signs off, the backend tells the agent's service, which picks the claim up from where it stopped and pays it; the claim shows as in progress in between. If the service can't be reached the sign-off stands, and the agent pays when it next starts or runs. The payment records who signed off and when.
- **Stopping and carrying on:** progress is saved after every step. A claim stopped partway, by a crash, a restart or Ctrl-C, carries on from its last step the next time the agent runs, on the same thread, and that is not a new attempt. A run first carries on with any claim that was stopped or signed off while the agent was away. When the service starts, it marks any dispatch or evaluation still running as stopped, and the next dispatch carries on the claims it left in progress, as the same attempts. Starting the service only pays claims already signed off, so a restart never spends on the model by itself.
- **Nothing twice:** a step can run again after a restart, so a repeat of a decision or a payment already recorded counts as done and returns the claim. A different decision on a decided claim is refused.
- **Approver:** with no sign-in, the person approving picks their name from the handlers who may approve over the limit. That handler is stored on the payment.
- **Referred claims:** a `refer` is a finished decision with no owner and no hand-off screen. It is `decided` and shows in the queue.
- **Cost and time:** the tokens and cost of each model call, as OpenRouter reports them, and the time the agent spends on each claim, leaving out any wait for a sign-off, are recorded with the dispatch or evaluation they happened in, or with no run. Each run's totals are its own, failed attempts included.
- **Failures:** a single model call that meets a provider error is retried (`step_retries`). A rate limit or a timeout makes the claim wait and carry on from its last saved step, as the same attempt; one still hitting them after its waits stays in progress for the next dispatch. A claim attempt that fails any other way before a decision (an error, more than 30 model calls, or no decision) goes back in the queue and the same dispatch tries it once more, straight away, on a new thread. An error after the decision does not undo it: the claim stays in progress, and the next run carries on from the failed step. A claim that fails twice (`max_attempts` in `agent.toml`) is left for a handler with the status "Needs a handler" and no decision. One failing claim never stops the run.

## 10. Open questions

- How a referred claim should reach a person (owner, notification, queue).
- How a claim left for a handler is put back in the queue or decided by a person.
