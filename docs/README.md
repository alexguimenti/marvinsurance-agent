# The docs

The project was built in steps. Each step has a **spec**, which says what to build and why (written as the request of a head of operations, IT or security would write it), and a **plan**, which says how, task by task, and is ticked off as it is done. `STATUS.md` says where things stand and what was checked live.

Read them in the order the project was built:

| Step | What it added | Spec | Plan |
|---|---|---|---|
| Pilot | The queue, a claim's page, the agent's live activity, approvals, the payout limit enforced by the payment code | [SPEC.md](specs/SPEC.md) | [PLAN.md](plans/PLAN.md) |
| Harness | A run that works the whole queue through a to-do list; a fresh claim agent for each claim | [HARNESS.md](specs/HARNESS.md) | [PLAN_HARNESS.md](plans/PLAN_HARNESS.md) |
| Cleanup | A pass over the repository, the backend and the front end after the harness | none | [CLEANUP.md](plans/CLEANUP.md) |
| Durable | Approvals that wait, and work that survives a restart | [DURABLE.md](specs/DURABLE.md) | [PLAN_DURABLE.md](plans/PLAN_DURABLE.md) |
| Swarm | N claim agents at once, evaluations and the Scoreboard | [SWARM.md](specs/SWARM.md) | [PLAN_SWARM.md](plans/PLAN_SWARM.md) |
| Improve | An improver agent, a judge, versions, handlers who sign in | [IMPROVE.md](specs/IMPROVE.md) | [PLAN_IMPROVE.md](plans/PLAN_IMPROVE.md) |
| Secure | The security changes before going on AWS | [SECURE.md](specs/SECURE.md) | [PLAN_SECURE.md](plans/PLAN_SECURE.md) |
| Context | The context layer over MCP, memory and the Gateway | [CONTEXT.md](specs/CONTEXT.md) | [PLAN_CONTEXT.md](plans/PLAN_CONTEXT.md) |
| Runtime | Sending claims back, and the agent on AgentCore Runtime | [RUNTIME.md](specs/RUNTIME.md) | [PLAN_RUNTIME.md](plans/PLAN_RUNTIME.md) |
| Copilot | The handler's assistant in a sidebar, with a confirmation before a payout | [COPILOT.md](specs/COPILOT.md) | [PLAN_COPILOT.md](plans/PLAN_COPILOT.md) |
| Reorganization | The repository reorganized to be read from outside: labs by week and day, docs split, a new README, the history rewritten | none | [PLAN_REORG.md](plans/PLAN_REORG.md) |

## Reference

- [TECH_STACK.md](TECH_STACK.md): what the project is built with and how it is deployed.
- [CODE_STANDARDS.md](CODE_STANDARDS.md) and [PROCESS.md](PROCESS.md): the rules the code was written under and how the work was done.
- [DESIGN.md](DESIGN.md): the visual style (the specs call it `BRAND.md`).
- [DECISIONS.md](DECISIONS.md): the choices that shaped the project, with why and where.
- [STATUS.md](STATUS.md): what works, what was checked and how, and the known limits.
- [diagrams/copilot_graph.html](diagrams/copilot_graph.html): the Copilot's graph and a sign-off end to end (open it in a browser).
