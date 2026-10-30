# SPEC: Farting Agent for Marvin FartInsurance

Status: pilot plus the harness (`docs/plans/HARNESS.md`). This is a course project. Nothing here is a real company, customer or payment.

## 1. Overview

Marvin FartInsurance sells travel insurance in three plans: **Economy**, **Premium** and **First**. Today every claim is reviewed by hand. The analyst reads the customer's story and documents, checks the customer's history and the policy, applies the policy wording, and then does one of three things:

- **Approve**, with a payout.
- **Decline**.
- **Refer** to another person, when the claim needs more judgment or more information.

We are building an AI agent, the **Farting Agent**, that works the claims queue the way a human analyst does. One run of the agent plans the queue as a to-do list and hands each claim to a fresh claim agent, which reads the claim, the policy, the customer's history and the sections of the policy wording and the claims manual it needs, makes a decision, and for an approval works out and prepares the payment. Staff get screens to watch and control the work.

The goal is to handle the queue accurately and consistently, as a careful person would.

> Naming note: the seed data originally used the name "UnderCover Travel Insurance". It was renamed to Marvin FartInsurance throughout `data/` (company name, plan names, handler email domains, policy wording, manual). The agent is the Farting Agent.

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

The agent is not handed whole documents. It finds sections with `search_documents` and `list_sections` and reads them with `read_section`, and the claim page lists what it read. `data/policy_wording_short.md` also exists and is not used.

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
| In progress | The agent is working on it |
| Awaiting approval | Decided and approved, but the payout is over $1,000 and waits for a person |
| Decided | Finished: approved and paid (simulated), declined or referred |
| Needs a handler | The agent failed on it twice and made no decision. A person has to take it |

## 6. Screens

The pilot needs these views. Everyone sees everything.

1. **Claims queue**: every claim with its current status, a limit for the next run, and the agent's to-do list for the current or latest run, ticked off as it works, with the run's summary and cost.
2. **Claim detail**: the full claim (story, items, documents, policy and customer context), the agent's decision, payout and rationale, the sections of the wording and the manual it read, and, for a claim left for a handler, how many attempts failed and why.
3. **Agent activity**: what the agent is doing on a claim, step by step, while it works (the trace of what it reads, checks and decides).
4. **Agent instructions**: the instructions for running the queue and for working a claim, so the team can see and review them.
5. **Approvals**: payments waiting for sign-off, with an approve action for authorized handlers.

Visual style follows `docs/DESIGN.md`.

## 7. Data and evaluation

All data lives in `data/` and is described in `data/README.md`: claims queue (25 claims), policies, customers, plans, handlers, company settings, policy wording and the claims manual.

**Evaluation data is off limits to the agent.**

- `data/evals/` holds the handlers' own decisions, plus held-out claims. They are used only to check the agent's work.
- The agent, its prompts, its tools and the screens must **never** read or show anything from `data/evals/`.
- The held-out claims are not in the queue. Keep them out of sight while writing prompts or rules, and score them afterwards.
- `data/security/` holds a prompt-injection test case. It is also kept out of the queue.

Evaluation scores two things per claim: the decision, and the payout.

## 8. Out of scope for the pilot

- **Sign-in and access control.** Everyone who uses the screens sees everything.
- **Real payments.** Payments are simulated.
- **Messages to customers.** Manual section 12, "Writing to the customer", is not applied.
- **Stopping a run midway** and a cost ceiling per run. The claim limit is the cost control.

## 9. Decisions made in the pilot

- **Trigger:** a "Run queue" button starts one run of the agent over the waiting claims, optionally capped at a number of claims ("Limit"); ticked claims, or one claim's "Run this claim" button, start a run limited to those claims. Only one run at a time; claims are worked one after another. Hosting is local only.
- **Runs:** the agent plans a to-do list, chooses the order, hands each claim to a fresh claim agent that reports back in one line, and ends with a summary for handlers. Before it finishes, a check refuses to close the run while a claim within its cap is still owed.
- **Approver:** with no sign-in, the person approving picks their name from the handlers who may approve over the limit. That handler is stored on the payment.
- **Referred claims:** a `refer` is a finished decision with no owner and no hand-off screen. It is `decided` and shows in the queue.
- **Failures:** a single model call that times out or meets a provider error is retried (`step_retries`). A claim attempt that fails (no decision, an error, a timeout, too many steps, or a payment that differs from the decision) is undone: its payment is deleted and the claim goes back in the queue, to be tried again later in the run. A claim that fails twice (`max_attempts` in `agent.toml`) is left for a handler with the status "Needs a handler" and no decision. One failing claim never stops the run, and no claim is decided or paid twice. A server that stops mid-run marks the run failed and puts its claims back in the queue when it starts again.

## 10. Open questions

- How a referred claim should reach a person (owner, notification, queue).
- How a claim left for a handler is put back in the queue or decided by a person.
