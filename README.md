# MarvInsurance: Marvin Agent and the Copilot

A course project from the Forward Deployed Engineer course. Nothing here is real: a made-up travel insurer, its customers and its claims. AI agents work the claims queue like a human analyst would, and a human handler stays in charge of the money.

It is kept here as a portfolio and as a reference for the next project. Nobody is expected to run it; the part worth reading is how it is put together, and the map below says where to look.

## What it shows

- **A claim agent and a dispatcher.** A dispatcher, which is code and no model, hands every claim to a claim agent of its own, up to 25 at once, and sums up the run from the records. The claim agent reads the claim, the policy and the customer's history, looks up the sections of the policy wording and the claims manual it needs, then approves, declines or refers. A payout over $1,000 waits for a handler, and the payment code, not the agent, enforces that.
- **Evaluation and self-improvement.** Evaluations on labeled sets, a judge that grades each explanation, and an improver agent that proposes changes which a handler approves and code keeps or rolls back.
- **The Copilot.** A handler's assistant in a sidebar on every page. It looks things up, starts work, sends decisions back, and signs off a payout only after the handler presses a button on a confirmation card. It acts as the handler, with the handler's rights.
- **It runs on AWS.** The app and the dashboard on Lambda, the agents on AgentCore Runtime, the context layer behind a Gateway, Memory, and credentials in AgentCore Identity. The records are on Supabase.

The Copilot's graph, and a sign-off from the click to the payment: [docs/diagrams/copilot_graph.html](docs/diagrams/copilot_graph.html) (open it in a browser).

```
 Handler's browser (React dashboard, Copilot sidebar)
        |  Supabase sign-in, then a bearer token on every request
        v
 App: FastAPI on Lambda  <------------------------->  Supabase Postgres
        |    starts runs and turns                      (records, checkpoints)
        v
 AgentCore Runtime
   |-- claim agent (a dispatcher fans out to one agent per claim)
   '-- Copilot (a LangGraph graph with a confirmation before a sign-off)
        |  tools over MCP, through the Gateway
        v
 Context layer on Lambda (definitions, the claims manual, policy wording, reference data)

 Models: OpenRouter. Traces: Langfuse.
```

## Where to find what

If you want to see how I did one thing, open the file next to it.

| To see how... | Open |
|---|---|
| A hand-written LangGraph agent with a human confirmation before an action | `backend/agent/copilot/graph.py` |
| The handler's sign-in reaches the tools but never a checkpoint | `backend/agent/copilot/asker.py` |
| A dispatcher that is code and no model, one agent per claim, N at once | `backend/agent/dispatcher.py` |
| The claim agent's graph and its tools | `backend/agent/graph.py`, `backend/agent/tools.py` |
| An AgentCore app with async tasks, run on a laptop or on Runtime | `backend/agent/copilot/entrypoint.py`, `runtime/copilot.py` |
| Retrying a request the platform refused with "Rate Exceeded" | `backend/agent/connection.py` |
| A payout limit enforced in code, not by the model | `backend/app/payments.py` |
| A confirmation card the app builds from its own records, not the model's words | `backend/app/copilot.py` |
| The context layer as an MCP server on Lambda, and through the Gateway | `backend/context/server.py`, `backend/agent/gateway.py` |
| Memory that remembers a handler's preferences | `backend/agent/copilot/memory.py` |
| Scoring against an answer key, and a judge for explanations | `backend/app/scoring.py`, `backend/app/judge.py` |
| An improver agent whose proposals a handler approves | `backend/agent/improver.py`, `backend/app/improvement.py` |
| The deploy: two runtimes from one project, packaged by a script | `agentcore/agentcore.json`, `agentcore/cdk/lib/cdk-stack.ts`, `scripts/prepare_runtime.py` |
| Everything checked with one command, in a clean copy | `scripts/check.mjs` |
| End-to-end tests with a scripted model and a throwaway database | `e2e/tests/` |
| A small design system in CSS variables, light and dark | `frontend/src/styles/tokens.css`, `docs/DESIGN.md` |

## The rest of the repository

- **`labs/`**: the course's notebooks, by week and day, with what each led to: [labs/README.md](labs/README.md).
- **`docs/`**: a spec and a plan for each step, in the order the project was built: [docs/README.md](docs/README.md). The decisions and why: [docs/DECISIONS.md](docs/DECISIONS.md). Where it stands: [docs/STATUS.md](docs/STATUS.md).
- **`docs/COMMANDS.md`**: how to set it up and run it (`npm run dev`), the tests (`npm run check`) and how to reseed the database.

```
backend/    the app and the records (app/), the agents (agent/, with agent.toml: the model and how it thinks), the context layer (context/), tests (tests/)
frontend/   the React dashboard and the Copilot sidebar (Vite, TypeScript, Vitest)
e2e/        Playwright tests, and the live checks
agentcore/  the AgentCore project: the runtimes, the credentials, the CDK stack
runtime/    what AgentCore Runtime starts: the agent's and the Copilot's entry points
scripts/    dev, check, build, reseed, packaging for the deploy
data/       the made-up company's data and the labeled claims
docs/       specs, plans, decisions, status, a diagram
labs/       the course's notebooks
```
