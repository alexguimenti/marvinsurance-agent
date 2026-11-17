# Starting the next project from this one

What is worth copying, and what to leave. Each row names the files; the decision behind it is in [DECISIONS.md](DECISIONS.md).

| You need | Copy | Leave |
|---|---|---|
| An AgentCore app with async work, run on a laptop or on Runtime | `backend/agent/copilot/entrypoint.py`, `runtime/copilot.py`, `agentcore/agentcore.json` | the claim-specific tools |
| A hand-written LangGraph agent that pauses for a human | `backend/agent/copilot/graph.py`, `asker.py`, `tools.py` | the claim tools in `tools.py` |
| A rule about money (or anything) that the model cannot break | `backend/app/payments.py`, `payout.py` | the claim tables |
| Many agents at once, one for each item, with a dispatcher that has no model | `backend/agent/dispatcher.py`, `worker.py` | the claim prompts |
| Secrets that a runtime fetches for itself | `backend/agent/identity.py` | none |
| A request retried when the platform says "Rate Exceeded" | `backend/agent/connection.py` and its test | none |
| One command that checks everything in a clean copy | `scripts/check.mjs` | the step list: write your own |
| A deploy packaged by a script, from one project to two runtimes | `scripts/prepare_runtime.py`, `agentcore/cdk/` | the stack's names |
| Tests that never call a model | `backend/agent/scripted.py`, `e2e/` | the claim fixtures |
| Scoring against an answer key, a judge, an improver a human approves | `backend/app/scoring.py`, `judge.py`, `backend/agent/improver.py` | `data/` |
| A small design system in CSS variables | `frontend/src/styles/tokens.css`, `docs/DESIGN.md` | the brand's names |
| The way the work was written down: a spec, then a plan ticked off | `docs/specs/`, `docs/plans/`, `docs/PROCESS.md` | the specs' contents |

## Order for a new project

1. Start from `docs/PROCESS.md` and `docs/CODE_STANDARDS.md`: write the spec as a request, then the plan.
2. Copy the dispatcher and the connection first; they have no claim in them.
3. Put the rule that must hold in code, with its test, before the agent exists.
4. Make the scripted model and `npm run check` work before the first real model call.
5. Deploy last, and from a clean copy: the first deploy found a missing file that a local run never would.
