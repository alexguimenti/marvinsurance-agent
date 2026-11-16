# Decisions

The choices that shaped the project, each with why it was made and where it lives. They are taken from the specs, the plans and `STATUS.md`; the plan of each step has the full argument.

## The payout limit is enforced by the payment code, not by the agent

A payout over $1,000 stays blocked until a handler approves it, and a handler's sign-off pays the claim in the backend with no help from the agent.

**Why:** a model can be talked into anything, and a rule about money should hold even if the agent is wrong, confused or attacked through a claim's text. The agent proposes; code decides what may be paid.

**Where:** `backend/app/payments.py`, `backend/app/payout.py`, `docs/specs/SPEC.md`

## A dispatcher with no model, and one claim agent for each claim

A dispatcher that is a graph with no model fans out one claim agent per claim, up to 25 at once, and sums up the run from the records.

**Why:** each claim's work stays focused and a run does not get more expensive as it goes; the summary comes from records, so it cannot be invented; and the fan-out is plain code that can be tested without a model.

**Where:** `backend/agent/dispatcher.py`, `backend/agent/worker.py`, `docs/specs/SWARM.md`

## The handler's sign-in reaches the tools in the graph's context, never in a checkpoint

The Copilot acts as the handler. Their token travels in the graph's context (`context_schema=Asker`), not in the config or the state.

**Why:** LangGraph saves the config's values and the state with every checkpoint, so a token in either would be written to the database. The context is not saved. The app's own checks still apply to every call the Copilot makes, so it can do nothing the handler could not.

**Where:** `backend/agent/copilot/asker.py`, `backend/agent/copilot/graph.py`

## The confirmation before a payout is a node that pauses the graph

The sign-off tool only asks. A node of its own calls `interrupt()` first, and the handler's answer comes back as `Command(resume=...)`; only "sign off" pays.

**Why:** LangGraph runs an interrupted node again from the top when it resumes, so nothing may come before the `interrupt()`. Giving the pause its own node keeps the payout out of the tool's reach, and "Cancel" pays nothing by construction.

**Where:** `backend/agent/copilot/graph.py`, `backend/agent/copilot/tools.py`, `docs/diagrams/copilot_graph.html`

## The confirmation card comes from the app's records, and goes stale if they change

The app builds the card from its own records when the graph pauses, and a sign-off from a card is refused with a 409 unless the same payment and amount are still held.

**Why:** a card the model wrote could show one amount and pay another. An independent review found that an old card could pay a different amount after a claim was sent back and held again; the check closes that.

**Where:** `backend/app/copilot.py`, `backend/app/copilot_routes.py`

## One Runtime session for each conversation

The app starts each turn and each answer with the conversation's id as the Runtime session id, and retries a conflict (the session is starting or stopping) four times, one second apart.

**Why:** a session keeps a conversation's process warm and its turns in order, and one turn at a time per conversation is enforced in the records with a conditional update, so two clicks cannot start two runs.

**Where:** `backend/app/copilot_client.py`, `backend/app/copilot.py`

## The dashboard polls and does not stream

The sidebar reads a conversation about once a second while its turn works, and not while it is idle. Answers are not streamed.

**Why:** the app runs on Lambda behind a Function URL, which cannot stream a response. The cards show progress as each step is recorded, which is what a handler needs to see.

**Where:** `frontend/src/copilot/useConversation.ts`, `docs/TECH_STACK.md`

## The success criteria are worked out in code

When asked whether the pilot's three criteria are met, the Copilot starts an evaluation, waits for it and reads the figures from the records. It never works them out itself.

**Why:** a figure the model computes can be wrong in a way that looks right. The percentages, the accuracy against the objective and the count of over-limit sign-offs come from the records and are checked by tests.

**Where:** `backend/agent/copilot/criteria.py`, `backend/tests/test_copilot_criteria.py`

## Every operation's inputs are required, and every docstring is one line

Each request field is required and may be null; none is optional. A test enforces this for every route.

**Why:** the Gateway turns the API into tools and cannot read "a number or null" (`anyOf`): it refuses every value. A limit that does not apply is 0, not absent.

**Where:** `backend/tests/test_entrypoint.py`, `docs/TECH_STACK.md`

## A request the platform refused for "Rate Exceeded" is sent again

`call` sends a request again, up to six times with growing, slightly random waits, when the platform answers 429 or "Rate Exceeded".

**Why:** a new AWS account allows 10 Lambda invocations at once, and 10 claim agents plus the dashboard went over it: claims were dropped and a Copilot turn could not even report its own failure. Such a request was refused before it ran, so sending it again is safe.

**Where:** `backend/agent/connection.py`, `backend/tests/test_connection_retry.py`

## Secrets live in AgentCore Identity, and each runtime fetches only what it needs

The agent fetches `OPENROUTER_API_KEY`, `UNDERCOVER_API_KEY` and `DATABASE_URL` on its first request; the Copilot fetches only the first and the last.

**Why:** a key in an environment variable or a package is a key in a place it can leak from. A runtime that never holds a key cannot lose it, and the Copilot does not need the agent's key to the app, because every call it makes carries the handler's own sign-in.

**Where:** `backend/agent/identity.py`, `agentcore/agentcore.json`, `runtime/copilot.py`

## No test calls a model

A scripted model stands in for the real one in every test and in the end-to-end suite, and fails on purpose on two claims so that recovery can be tested.

**Why:** tests that call a model are slow, cost money and give different answers. What a model does is checked by evaluations on labeled claims, with their own measures; what the code does around it is checked by tests that are the same every time.

**Where:** `backend/agent/scripted.py`, `e2e/tests/`, `scripts/check.mjs`
