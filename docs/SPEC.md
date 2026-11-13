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

- The approval limit is **$1,000** to start with (`payout_approval_limit` in `company.json`). It is a **setting** a senior handler can change on the Settings page. Each decision stores the limit it was made under, so a new limit never reaches back to a payment already held.
- Any payout **above $1,000** needs a person's manual approval. The agent may decide to approve such a claim, but the payment **waits** for a handler's sign-off.
- The limit is enforced by the **system**, not by the agent's good behavior. The payment step must block any payout over the limit until it is approved, even if the agent tries to pay it.
- Handlers who may approve over the limit are listed in `data/handlers.json` (`can_approve_over_limit`): Dana Whitfield only. The backend checks who is signed in on every action, so a handler who may not is refused, whatever the screen shows.
- A valid claim over the limit is still an **approve** decision. Only the payment is held.
- Payments are simulated (see section 8).

## 5. Claim lifecycle

Every claim has one status:

| Status | Meaning |
|---|---|
| Waiting | In the queue, not yet picked up |
| In progress | The agent is working on it, or has been stopped partway and will carry on |
| Awaiting approval | Decided and approved, but the payout is over $1,000. The agent has stopped and waits for a person |
| Decided | Finished: approved and paid (simulated), declined or referred |
| Needs a handler | The agent failed on it twice and made no decision. A person has to take it |

An evaluation's copies of claims have the same statuses, but they never show in the queue and nobody signs them off: an approval held for sign-off counts as finished.

## 6. Screens

Handlers sign in with their email and password (Supabase Auth). Everyone signed in sees everything; what a handler may *do* depends on who they are (section 6.1).

1. **Claims queue**: every claim with its current status; a choice of N, the claim agents at once (1 to 25, 3 to start with), and a button that starts a dispatch; and the run panel for the latest dispatch: each of its claims and where it stands, its model cost and its agent time, and, once it ends, a summary built from the records: how many were approved, declined and referred, each payout waiting for a sign-off, and each claim left for a handler or still in progress.
2. **Claim detail**: the full claim (story, items, documents, policy and customer context), the agent's decision, payout and rationale, the sections of the wording and the manual it read, and, for a claim left for a handler, how many attempts failed and why.
3. **Agent activity**: what the agent is doing on a claim, step by step, while it works (the trace of what it reads, checks and decides).
4. **Configuration**: the claim agent's versions (its instructions, model and reasoning effort), which one is current, who proposed and approved each, and the difference between any version and the one before it; and the improver's recipe versions. The dispatcher is code and has none.
5. **Approvals**: payments waiting for sign-off, with an approve action for authorized handlers. A handler can only sign off for now; rejecting a held payout comes later. The claim then shows who signed off and when.
6. **Scoreboard**: every evaluation, newest first, with its set, N, model and reasoning effort, and the measures from the team's labs: decision accuracy, accuracy (the decision, and a payout within a dollar), the cost of mistakes, the straight-through rate, model cost per claim, time per claim and the time for the whole run. A handler starts an evaluation here. Each opens to its claims, the handler's decision and payout beside the agent's, the misses marked, each linked to its page. A copy's page says it is a copy and has no sign-off.

7. **Improvement**: the rounds of the improver as they happen (its stage, the evaluations it runs, its cost against the round's spending limit), its proposals with the reason for each and an approve or reject action for senior handlers, and each version's scores. A handler starts and stops a round here.
8. **Settings**: the approval limit, the objectives, the noise and tolerance of each measure, the round spending limit, the claim agents at once, and the appearance (light, dark, or the same as the computer). Every change records who made it and when; only a senior handler can change a platform setting.

### 6.1 Sign-in and who may do what

- Reading needs no sign-in on the API; every action (start a dispatch, an evaluation or a round, sign off, decide a proposal, change a setting) needs a signed-in handler, and the backend refuses the rest. A session that ends signs the handler out with a message.
- Starting, stopping and watching are for any handler. Signing off over the limit, deciding an improvement proposal and changing a setting are for a senior handler (`can_approve_over_limit`).
- The pages say why a button is not there: Marcus Oyelaran and Priya Raman see the settings and the proposals and are told they cannot change them.

### 6.2 The judge's grade

An evaluation also gets a **grade** for each claim, A to F, from Jev, a judge model. It compares the agent's explanation with the handler's reason for the same claim (`reference` in the answer key) and answers three questions: how well the explanation matches, whether it rests on the same grounds and whether it names the same section. The letter is the most likely grade, the score the weighted average; a claim with no decision gets F, and a claim the judge could not grade is left out of the average rather than counted as zero. The Scoreboard and an evaluation's page show the grade beside accuracy.

Visual style follows `docs/DESIGN.md`.

## 7. Data and evaluation

All data lives in `data/` and is described in `data/README.md`: claims queue (25 claims), policies, customers, plans, handlers, company settings, policy wording and the claims manual.

**Evaluation data is off limits to the agent.**

- `data/evals/` holds the handlers' own decisions, plus held-out claims. They are used only to check the agent's work: by seeding, by the scoring and by the judge.
- Seeding stores the held-out claims, out of the queue, and the handlers' decisions and reasons, in tables of their own that no claim operation reads. The claim agent, its prompts and its tools never read them; the backend scores and grades each evaluation from them.
- The **improver** may read the evaluation set's decisions and reasons, through the backend's `read_results`, to learn what the claim agent got wrong. Nothing in the agent, the improver's included, reads the held-out set's decisions or reasons: only the code that scores a version on it does.
- Two labelled sets: the **evaluation set**, the 25 claims of the queue, and the **held-out set**. Each has five `tiny` claims for a cheap check.
- An **evaluation** works its own copies of a set, whole or tiny, through the same dispatcher and claim agent as a dispatch. Each copy has a new id, such as `E7-CLM-1003`. The queue stays as it was.
- `data/security/` holds a prompt-injection test case. It is kept out of the queue.

Evaluation scores the decision and the payout of each claim. A claim the agent didn't decide counts as a referral. The cost of a mistake: a wrongful decline $1,500, a wrongful approval its payout (paid or not), a needless referral $50, a wrong amount on a right approval the difference. The straight-through rate is the share of claims not referred.

## 8. Out of scope for the pilot

- **Real payments.** Payments are simulated.
- **Messages to customers.** Manual section 12, "Writing to the customer", is not applied.
- **Stopping a dispatch or an evaluation midway.** N and the tiny sets are the cost control. A round has a spending limit and can be stopped.

## 9. Decisions made in the pilot

- **Trigger:** "Start dispatch" on the queue page starts a dispatch with N claim agents at once; "Start evaluation" on the Scoreboard starts an evaluation. Only one dispatch or evaluation runs at a time, and a second start is refused. The backend passes N and the set to the agent's service, which answers at once and works in the background; the dashboard says so if the service is not running. `uv run -m agent CLM-1001` works one claim. Hosting is local only.
- **Dispatches:** a dispatch works every claim left in progress and every waiting claim, oldest submitted first, with up to N claim agents at once. Code chooses the claims and keeps track of the run, with no model. Each claim keeps its own thread. A claim the service is resuming after a sign-off is left to it. The run's summary is built from the records.
- **Paying:** paying is a step of the claim agent's work for every approval, straight after the decision when no sign-off is needed. When the backend says a sign-off is needed, the agent stops and waits, keeping everything it has worked out. A handler's sign-off pays the claim: the backend records the payment, with who signed off and when, in the same request and the claim is decided. The sign-off needs no help from the agent's service. After a sign-off nothing in the agent calls the payment operation for that claim: if it is resumed it finishes its work without paying.
- **Stopping and carrying on:** progress is saved after every step. A claim stopped partway, by a crash, a restart or Ctrl-C, carries on from its last step the next time the agent runs, on the same thread, and that is not a new attempt. A run first carries on with any claim that was stopped while the agent was away. When the service starts, it marks any dispatch or evaluation still running as stopped, and the next dispatch carries on the claims it left in progress, as the same attempts. Starting the service spends nothing on the model by itself.
- **Nothing twice:** a step can run again after a restart, so a repeat of a decision or a payment already recorded counts as done and returns the claim. A different decision on a decided claim is refused.
- **Approver:** the signed-in handler, who must be allowed to approve over the limit. That handler is stored on the payment.
- **Referred claims:** a `refer` is a finished decision with no owner and no hand-off screen. It is `decided` and shows in the queue.
- **Cost and time:** the tokens and cost of each model call, as OpenRouter reports them, and the time the agent spends on each claim, leaving out any wait for a sign-off, are recorded with the dispatch or evaluation they happened in, or with no run. Each run's totals are its own, failed attempts included.
- **Failures:** a single model call that meets a provider error is retried (`step_retries`). A rate limit or a timeout makes the claim wait and carry on from its last saved step, as the same attempt; one still hitting them after its waits stays in progress for the next dispatch. A claim attempt that fails any other way before a decision (an error, more than 30 model calls, or no decision) goes back in the queue and the same dispatch tries it once more, straight away, on a new thread. An error after the decision does not undo it: the claim stays in progress, and the next run carries on from the failed step. A claim that fails twice (`max_attempts` in `agent.toml`) is left for a handler with the status "Needs a handler" and no decision. One failing claim never stops the run.
- **A key out of credit:** when OpenRouter answers 402 (the key's credit or daily limit cannot pay for a call), that is nobody's failure. The claim stays in progress with no failed attempt, the run starts no more claims and ends stopped saying so, and a round ends with the same message. Raising the key's limit and starting again carries on. Each call may produce at most `max_tokens` (16,384) tokens, because OpenRouter holds credit back for the most a call may produce, and a model's own ceiling (131,072 for DeepSeek) would need a rich key.

## 10. Open questions

- How a referred claim should reach a person (owner, notification, queue).
- How a claim left for a handler is put back in the queue or decided by a person.

## 11. Improving the claim agent

A **round** is the improver's work, from one start to one verdict. It is its own agent, with a recipe (the lab's first, `backend/agent/recipe_v1.md`) and goals, not steps, in its instructions.

- **Stages:** the first evaluations of the current version (evaluation set once, held-out set twice, so the noise of a version is measured before any change is judged), the improver proposing, a handler deciding, the new version evaluated on both sets, and the verdict. It may also propose a change to its own recipe, which a handler decides the same way.
- **A proposal** is a new version of the claim agent's instructions, model or reasoning effort, with a reason. The model must be one the platform allows (`data/models.json`); the improver is told when it is not, before a handler is ever asked. Nothing becomes current until a senior handler approves it and the round keeps it.
- **Keep or roll back** is code, not the improver: a version is kept, on its held-out scores against the current version's, only when it has a grade, meets every objective, is no worse than the tolerance on any measure and improves at least one by more than its noise; otherwise it is rolled back, with the reason. Accuracy and grade compare as differences, cost and time as fractions. The round's report shows the scores before and after, the noise and the tolerance beside them.
- **Cost:** a round has a spending limit (50 cents to start with). New claims stop once it is spent or the round has ended, and the evaluation ends as stopped. The dashboard says what a round may spend before it starts.
- **One at a time:** one round, dispatch or evaluation runs at a time, and a refusal says which. A round waiting for a handler holds nothing, so a dispatch can run meanwhile. When the service starts, a round left working shows as stopped.
- **Runs record their version:** each dispatch and evaluation records the configuration version it worked with, and the round that asked for it.

## 12. Security before the app has a public address

- **The agent's API key.** The key is `UNDERCOVER_API_KEY` in `.env`, a random value of at least 32 characters. Everything on the agent's side that calls the backend (the claim agent's tools, the dispatcher, the improver and the agent's service) sends it in an `X-API-Key` header. A route only the agent's side calls refuses a request without the right key with a 401, and so does any request whose key does not match (compared in constant time). The reads the dashboard and the agent share take either the key or a signed-in handler; every other route needs a signed-in handler. The key never stands in for a handler: signing off, starting a dispatch, an evaluation or a round, reviewing a proposal and changing a setting still need a signed-in handler. `GET /api/health` is open. With no key set the backend still starts and handlers can use the dashboard, but every request that needs the key is refused, and the agent's service refuses to start.
- **The check for hidden instructions.** Before the claim agent's first model call, a second model (GPT-6 Luna at effort `none`, or the one model in `MODEL_NAME` with no effort) reads everything the customer wrote and says whether it tries to instruct whoever handles the claim. When it finds an instruction the claim agent's model never sees the claim: the agent records a referral with a payout of zero, the check's reason as the rationale and "Security check" as the section. When it finds nothing the claim agent works the claim as before. The check runs once for each claim, at the start; work that carries on from saved progress does not run it again, and a claim that starts again from its first message is checked again. Each call of the check, retries included, is recorded with its cost as one of the claim's model calls, and the claim's work shows its result first. An answer that cannot be read is asked for again, up to three attempts; if it still fails, or errors, the claim fails as any other error does. The check sits outside the versioned configuration, so the improver cannot change it. CLM-1901, the security team's claim, is in the queue with its policy and customer; its handler's decision is not seeded, so it is in neither evaluation set.
- **The agent's own API.** `GET /api/agent/openapi.json` is an OpenAPI document with exactly the claim agent's six operations (`get_claim`, `get_policy`, `get_claim_history`, `calculate_payout`, `record_decision`, `pay_claim`), generated from the app's routes, with no `servers` entry and no key input. It needs the key or a signed-in handler. The payment operation refuses any amount over the approval limit with a 403 that names the limit, before it looks at the claim.
- **A sign-off pays.** A handler's sign-off records the payment, with who and when, and the claim is decided, in the one request. It does not need the agent's service.

## 13. The context layer, memory and the Gateway

- **Three sources, three owners.** The claim agent works from the *context layer* (the business's data from other systems: what the data means, how the work is done and where each file came from; data governance owns it and the agent only reads it), its *memory* of each customer (its own notes, in AgentCore Memory, read and written directly, limited by its AWS credentials) and the *database* (the system of record, reached only through the app).
- **The context layer is its own service.** An MCP server (`backend/context/`) over seven files, each naming the system it comes from, its owner and when it was last reviewed: the data dictionary, the glossary, the plans, the claim rules, the reference data, the policy wording and the claims manual. Its tools are read-only and take no optional inputs: `lookup_term`, `describe_fields`, `get_plan`, `get_claim_rules`, `search_reference_data`, `search_documents` and `read_section`. Every answer names its file with that file's source. It reads its files once, when the process starts. It runs on Lambda behind a Function URL with `AWS_IAM` auth, and on a laptop at `http://127.0.0.1:8006/mcp`.
- **The app no longer reads those files while it runs.** Seeding loads the policy wording and the claims manual into the documents table, with each version from its front matter. The app's `search_documents` and `read_section` operations are gone, since the agent reads the documents in the context layer.
- **Two routes to the same tools.** With `UNDERCOVER_GATEWAY_URL` set, the model's tools and the payment are the Gateway's MCP tools, over streamable HTTP, signed with AWS Signature Version 4 for `bedrock-agentcore`, so the Gateway's Policy sees them; they are named `<target>___<operation>` with the targets `undercover-app` and `undercover-context`. Without it the agent calls the app direct with its key and the context layer at `UNDERCOVER_CONTEXT` (`http://127.0.0.1:8006/mcp`) with no signature. Either way the model gets the same tools with the same names and inputs, and takes no claim id, policy or customer from the model. The agent's own bookkeeping (starting a claim, its steps, model calls, work times, failures, the security check's read and referral, runs, versions and the improver) goes straight to the app on both routes.
- **A refusal is never an answer that looks like a success.** When the app or the context layer refuses a tool call the model is shown the reason. When the Gateway's Policy refuses a tool call, the refusal is recorded on the claim as the Gateway's and the claim's work goes on. When the Gateway refuses the payment, which happens only when the approval limit setting has been raised above the Policy, the claim waits for a handler's sign-off as an approval over the limit does, and the sign-off panel says that the Gateway refused it. A handler's sign-off then pays it.
- **A complete decision record (claims manual section 14).** The app keeps the latest payout calculation of each claim's current attempt. `record_decision` takes who made the decision (the agent fills it in: "UnderCover Agent, configuration version N", or "Security check"), and refuses a record that falls short with a 422 that names section 14 and says what is missing: a reason, a section or a maker that is blank, an approval that pays nothing, a decline or a referral that pays something, and, for an approval, no calculation on file for this attempt or a payout that differs from it. The record holds the decision, the payout, the calculation (the lines as `calculate_payout` gave them, none for a decline or a referral), the sections relied on, the reason, who made it and when, and the claims manual's version in force; a handler's sign-off adds who and when.
- **Memory of each customer.** When a claim ends with a decision the agent recorded, the graph saves two events, in code every time: the claim (its summary, the benefit, the date and place of the incident and the items) as the customer's message and the decision with its payout, section and reason as the agent's, with the customer as actor and the claim as session. The story and the documents stay on the claim. Nothing is saved for a claim the security check referred or for an evaluation's copy. The model gets `search_memory(query)`, which searches only under its claim's customer and leaves out the summary of the claim being worked; the tool is there only when memory is on and the run is not an evaluation. The claim history in our records is still what counts for repeat claims. When AgentCore cannot be reached, a save logs a warning and the claim keeps its decision, and a search gives the model the error as a refusal.
- **Every step on the claim page.** Each tool call (named as the Gateway names it on that route), each lookup in the context layer (what was looked up, what it found and the file it came from, with the file's system, owner and review date), each search of memory and each save to memory, and each refusal (the Gateway's, the app's, the context layer's or memory's) is a step of its own. A *Context used* panel lists what the agent looked up, once each, grouped by file with each file's source, and the decision panel shows the whole record with its calculation line by line.

## 14. Sending claims back, and the agent on AgentCore Runtime

- **A large approval can come back.** An approval that waits for sign-off (a payout over the approval limit, or a payment the Gateway refused) pauses the claim graph with LangGraph's `interrupt()`, its place saved on the claim's thread. A claim is paused when its thread has an interrupt waiting for an answer. An evaluation's copy does not pause: nobody signs off a copy. A senior handler answers on the claim page: **Sign off**, as before, or **Send back**, with a note saying what to look at again (required, up to 1,000 characters). A sign-off or a send back goes through only while the claim is waiting for sign-off, so one made at the same moment as the other loses with a 409.
- **What a send back does.** The app keeps the decision that was sent back on the claim, with the note, who sent it back and when; clears the claim's current decision, its held payment and its payout calculation; records the note as a step; and puts the claim in a new status, `sent_back`. It then asks the agent to resume the claim. The agent takes a sent-back claim once, whoever asks first (its resume, or a dispatch): the claim goes to in progress, and the agent tells the app every minute that it is working, so a dispatch leaves it alone. A dispatch takes sent-back claims along with the waiting ones, so a send back the agent never received (it was down, or its session had ended) is worked by the next dispatch, which resumes the paused thread with the note; the claim page says it is waiting for the next dispatch.
- **How the agent decides again.** The paused node reads the answer from the app's record. On a send back the graph forgets the earlier decision first, so nothing can pay it, gives the claim agent its full allowance of model calls again and adds a message from the handler, naming who sent it back, with the note. The message says how to treat it, in the agent's code and not its instructions (so the improver cannot drop it): as a senior handler's review, checked against the policy wording and the claims manual like everything else, with the claim referred to a handler, giving the reason, when the wording does not support what the note asks. The new decision goes through the same checks as any other: an approval needs its calculation, a payout over the limit waits for sign-off again, and one within the limit is paid. A claim can be sent back more than once.
- **After a sign-off** the claim agent's work ends as for any decision and the claim is saved to memory when memory is on. The payment stays the app's: the agent never pays over the limit. A sign-off the agent never hears of stands (the payment is made); the thread stays paused and nothing is saved to memory, which `STATUS.md` lists among the known limits.
- **The agent is an AgentCore app.** `agent/entrypoint.py` makes `app = BedrockAgentCoreApp()` with one entrypoint. Each payload names an operation: `work_run` (a dispatch or an evaluation the app created), `work_round`, `carry_round_on` (after a handler's review), `resume_claim` and `health`. It answers `{"accepted": true}` when it has taken the work, or `{"refused": "<reason>"}`; an unknown operation or a bad input is a 400 that says what is wrong. Work runs on a thread of its own, registered as an async task, so the app's `/ping` says `HealthyBusy` while any work is going and a Runtime session lasts as long as its work. Nothing happens when the agent is imported or its app starts: on its first request it gets ready once in its process (on Runtime it reseeds Python's random numbers and fetches its credentials from AgentCore Identity), then checks it has the agent's key and opens its checkpoint pool. Off Runtime a request without the key (`Authorization: Bearer`) is refused with a 401, apart from the health check; on Runtime AWS has already checked the caller may invoke the runtime.
- **The app keeps one piece of the agent's work at a time**: a dispatch, an evaluation, or a round while it works (open, with no proposal waiting for a review). The app creates the run, the round, or records a handler's review itself, under one lock row that two starts at the same moment take turns on, and then asks the agent to work it by its id; a handler who asks while something is going gets a 409 that names it. Resuming a claim runs beside anything. When the agent did not take the work (it refused, the request never got there, or it answered with an error) the app undoes it: a run is stopped with the reason, a new round is stopped, a review is cleared (a version an approval made is removed, and a recipe it made current gives way to the one before). A timeout or an answer cut off changes nothing, and the handler is told to reload the page.
- **Work the app has not heard about for five minutes counts as stopped** (a session ended, or the agent on the laptop restarted), counting from the later of when the app started it and the agent's last word. The next start stops it with the reason "The agent stopped answering", a round with its evaluation, and goes ahead. The app never marks a round's evaluation stopped on its own. When the agent's work on a run or a round fails it stops only that run or round. The dashboard shows a run's or a round's reason for stopping.
- **How the app reaches the agent.** By default over HTTP: `POST /invocations` at `UNDERCOVER_AGENT` (`http://127.0.0.1:8080`) with the agent's key as a bearer token, in 10 seconds. When `UNDERCOVER_AGENT_RUNTIME` is the ARN of the agent's runtime, with boto3's `invoke_agent_runtime` instead, with no key, in a session of its own for each request (an id of at least 33 characters), one attempt with no retries, 5 seconds to connect and 20 to read.

## 15. The success criteria and the Copilot

- **The three success criteria we agreed** when the pilot started:
  1. **Straight through:** at least 70% of claims handled straight through on the whole held-out set, measured by the straight-through share our evaluations already record.
  2. **Accuracy:** at least as accurate as we agreed: the accuracy our evaluations already record on the whole held-out set is at or above our accuracy objective in Settings.
  3. **Over the limit:** every payout over the approval limit approved by a person. Every claim in our queue paid more than the approval limit has a handler's sign-off on its payment. When none has been paid over the limit yet, this one is met, and the answer says why.
- **The UnderCover Copilot** is an agent for the handlers, in a sidebar on the right of every signed-in page (open the first time a handler signs in, a slim tab when collapsed, over the page on a narrow window). It looks things up in our records and the context layer, starts a dispatch or an evaluation, sends a held decision back with a note and signs off a held payout. For anything else (a setting, the configuration, an improvement round) it says it cannot do that yet and where on the dashboard to do it.
- **It acts as the handler who asked.** Every call it makes to the app carries the handler's own sign-in and never the agent's API key, so it can do exactly what the handler can on the dashboard, and the records name the handler. A request that carries the key is the agent's, whatever else it carries, and the Copilot's routes refuse it. Each turn, and each answer to a confirmation, starts by asking the app who is signed in; if the app does not accept the sign-in, it ends there, before any call to the model. The sign-in is never saved: not in a conversation's saved state, its steps, the memory or a log. The dashboard renews the sign-in before sending when it has less than 20 minutes left. A conversation belongs to the handler who started it.
- **It asks before it pays.** The Copilot checks in code that the handler may sign off, then the conversation pauses (`interrupt()` in a node of its own) and the sidebar shows a confirmation card: the claim, the customer, the amount and the agent's reason, read from our records, never from what the model wrote. Nothing is paid until the handler presses Sign off and pay; Cancel pays nothing. The pause is saved, so the handler can answer it after a reload or another day, and nothing before it runs again. While a confirmation waits, the message box asks for the answer first.
- **It waits in code.** After it starts a dispatch or an evaluation it waits on that run by checking the app every 3 seconds, never by calling the model again and again, and after 15 minutes says the work is still going and where to follow it.
- **The criteria, worked out in code.** To answer "Are we meeting the success criteria we agreed?" it starts an evaluation of the whole held-out set with the claim agents in the settings, waits for it, and answers with a table of each criterion, its target, its figure and whether it is met. Every figure comes from our records (the evaluation's measures, the accuracy objective in Settings, and the app's count of payouts over the limit and how many carry a handler's sign-off), worked out in code. If the evaluation is refused, fails, stops or is still going after 15 minutes there are no figures for the first two, and the third is still given.
- **Conversations are kept.** Each conversation's state is saved after every step in Supabase, with its steps and where each turn stands (working, waiting for a confirmation, done, failed or stopped). One turn at a time: the app refuses a second message while the Copilot works on one. A turn working 20 minutes after it started, or after its confirmation was answered, shows as stopped; a waiting turn is never stopped.
- **It remembers each handler** in an AgentCore Memory of its own (`undercover_copilot_memory`), with a namespace for each handler, when `UNDERCOVER_COPILOT_MEMORY_ID` is set. The handler is whoever the sign-in says. After each turn only the handler's message and the answer are saved.
- **Not for now:** streaming answers, stopping a turn partway, the Copilot reading the claim agent's memory, sharing, renaming or deleting conversations, changing settings through the Copilot, Langfuse tracing for it.
