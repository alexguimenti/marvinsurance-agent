# SPEC: Farting Agent for Marvin FartInsurance

Status: draft, pilot scope. This is a course project. Nothing here is a real company, customer or payment.

## 1. Overview

Marvin FartInsurance sells travel insurance in three plans: **Economy**, **Premium** and **First**. Today every claim is reviewed by hand. The analyst reads the customer's story and documents, checks the customer's history and the policy, applies the policy wording, and then does one of three things:

- **Approve**, with a payout.
- **Decline**.
- **Refer** to another person, when the claim needs more judgment or more information.

We are building an AI agent, the **Farting Agent**, that works the claims queue the way a human analyst does. It reads each claim, the policy, the customer's history and the policy wording, makes a decision, and for an approval it works out and prepares the payment. Staff get screens to watch and control the work.

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
| The customer: contact details and claim history | `data/customers.json` |
| The plan limits, waiting periods, deductibles and add-on effects | `data/plans.json` |
| Company settings (approval limit, filing window, look-back) | `data/company.json` |
| The policy wording | `data/policy_wording.md` |

`data/policy_wording_short.md` also exists. The pilot uses the full `policy_wording.md` unless this changes.

### 3.2 Outputs

For every claim the agent produces:

1. A **decision**: `approve`, `decline` or `refer`.
2. For an approval, a **payout amount** and its calculation, following the payment steps in the policy wording, section 10 (eligible amount, minus third-party payments, minus deductible, capped at the benefit maximum).
3. A **rationale** of two or three short sentences that explains the decision and **names where in the policy wording it relies** (for example, "section 5, theft must be reported to police within 24 hours").

### 3.3 Behavior rules

- Decisions must follow the rules and the policy wording. The agent does not invent rules.
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

## 6. Screens

The pilot needs these views. Everyone sees everything.

1. **Claims queue**: every claim with its current status.
2. **Claim detail**: the full claim (story, items, documents, policy and customer context), the agent's decision, payout and rationale.
3. **Agent activity**: what the agent is doing on a claim, step by step, while it works (the trace of what it reads, checks and decides).
4. **Agent instructions**: the instructions the agent is working with, so the team can see and review them.
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

- **The claims manual** (`data/claims_manual.md`). The agent works from the policy wording alone. This means rules that live only in the manual (such as the third claim in 12 months or losses to wild animals) are not applied by the agent in the pilot.
- **Sign-in and access control.** Everyone who uses the screens sees everything.
- **Real payments.** Payments are simulated.
- **Messages to customers.**

## 9. Open questions

To settle as the build progresses:

- Hosting and how the agent is run. The stack itself is in `TECH_STACK.md`.
- How the agent is triggered: process the whole queue at once, claim by claim, or continuously.
- Who is recorded as the approver of an over-limit payment, given there is no sign-in.
- Whether a referred claim has an owner or a hand-off screen in the pilot.
