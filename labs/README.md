# The course's labs

These are the notebooks I ran during the Forward Deployed Engineer course, one folder for each week and day. The project in the rest of this repository grew out of them: each lab is a step, and the last column says where its idea ended up.

How to read them:

- They are as they ran, with the values that identify my AWS account (the account id, a Supabase project, Function URLs) replaced by examples. Output cells that print such values show the examples. Nothing in them runs against anything today.
- Each reads the `.env` in the repository root. A path to the project (`../../../data`) is relative to the notebook's own folder.
- **The course's** are the instructor's notebooks, as given; **mine** are the same lessons rebuilt on this project's own data (the `_mex` notebooks), in English or Portuguese as I wrote them.
- A few have files beside them: `labs/week1/day5/lab1_desk/` and `lab2_desk/` (the week 1 labs' working folders), `labs/week2/day2/knowledge_mcp.py` (the claims manual as an MCP server), `labs/week3/week3day4_aws.py` (the AWS helpers the week 3 notebooks import), `labs/week3/monitor.py` (a monitor of AWS spend; `uv run labs/week3/monitor.py`), and `labs/week3/day4/undercoverlab/` (the small AgentCore project the Day 4 lab created).

| Day | Notebook | Whose | What it teaches | Where it ended up |
|---|---|---|---|---|
| Week 1, Day 2 | [week1day2_mex.ipynb](week1/day2/week1day2_mex.ipynb) | mine | A claim decided by one model call with a structured answer (`ClaimDecision`), through OpenRouter | `backend/agent/models.py` |
| Week 1, Day 4 | [week1day4_mex.ipynb](week1/day4/week1day4_mex.ipynb) | mine | Reading only the sections of the policy wording a claim needs, and the plan's limits | `backend/context/policy_wording.md`, `backend/agent/tools.py` |
| Week 1, Day 5 | [week1day5_mex.ipynb](week1/day5/week1day5_mex.ipynb) | mine | A claims handler agent on the Anthropic SDK, working a folder of claims (lab 1) | `backend/agent/graph.py` |
| Week 1, Day 5 | [week1day5_mex_2.ipynb](week1/day5/week1day5_mex_2.ipynb) | mine | The same job with LangChain's `create_agent`, tools and the handover note (lab 2) | `backend/agent/tools.py`, `backend/agent/instructions_claim.md` |
| Week 2, Day 1 | [week2day1_mex.ipynb](week2/day1/week2day1_mex.ipynb) | mine | Experiments on the harder claims: a dataset, scores and traces in Langfuse | `backend/agent/tracing.py`, `backend/app/scoring.py` |
| Week 2, Day 2 | [week2day2_mex.ipynb](week2/day2/week2day2_mex.ipynb) | mine | A vector database (Qdrant) behind an MCP server that looks up the claims manual | `backend/context/server.py` |
| Week 2, Day 3 | [week2day3_mex.ipynb](week2/day3/week2day3_mex.ipynb) | mine | A hand-written LangGraph graph: a payout tool, saving the decision, state | `backend/agent/graph.py` |
| Week 2, Day 4 | [week2day4.ipynb](week2/day4/week2day4.ipynb) | the course's | Agent swarms: a dispatcher that hands claims to many agents at once | `backend/agent/dispatcher.py` |
| Week 2, Day 4 | [week2day4_mex.ipynb](week2/day4/week2day4_mex.ipynb) | mine | The dispatcher on this project's 25 harder claims, one trace for each parallel level | `backend/agent/dispatcher.py`, `backend/agent/worker.py` |
| Week 2, Day 5 | [week2day5.ipynb](week2/day5/week2day5.ipynb) | the course's | Self-improvement: an improver agent that changes the claim agent, and a judge | `backend/agent/improver.py`, `backend/app/judge.py` |
| Week 2, Day 5 | [week2day5_mex.ipynb](week2/day5/week2day5_mex.ipynb) | mine | The improver and the judge on this project's data, with handlers' decisions in Supabase | `backend/app/improvement.py`, `backend/app/evaluations.py` |
| Week 3, Day 1 | [week3day1.ipynb](week3/day1/week3day1.ipynb) | the course's | An AWS session, MFA, budgets and the Bedrock models | `labs/week3/week3day4_aws.py` |
| Week 3, Day 2 | [week3day2_mex.ipynb](week3/day2/week3day2_mex.ipynb) | mine | A screener before the claims agent (guardrails): blocked terms, then a second model | `backend/agent/screening.py` |
| Week 3, Day 2 | [week3day2_mex2.ipynb](week3/day2/week3day2_mex2.ipynb) | mine | The app and the dashboard on AWS Lambda behind a Function URL | `scripts/build.mjs`, `backend/app/main.py` |
| Week 3, Day 3 | [week3day3.ipynb](week3/day3/week3day3.ipynb) | the course's | The context layer on Lambda as an MCP server, the Gateway, and AgentCore Memory | `backend/context/`, `backend/agent/gateway.py`, `backend/agent/memory.py` |
| Week 3, Day 4 | [week3day4_mex_lab1.ipynb](week3/day4/week3day4_mex_lab1.ipynb) | mine | A first agent on AgentCore Runtime: sessions, cold starts, logs, tearing it down (`undercoverlab/`) | `backend/agent/entrypoint.py`, `runtime/main.py` |
| Week 3, Day 4 | [week3day4_mex_lab2.ipynb](week3/day4/week3day4_mex_lab2.ipynb) | mine | The claim agent on Runtime, with the app calling it | `backend/app/agent_client.py`, `agentcore/agentcore.json` |
| Week 3, Day 5 | [week3day5_mex_lab1.ipynb](week3/day5/week3day5_mex_lab1.ipynb) | mine | Deploying the second runtime (the Copilot), memory, the Gateway and the permissions between them | `runtime/copilot.py`, `agentcore/cdk/lib/cdk-stack.ts` |
| Week 3, Day 5 | [week3day5_mex_lab2.ipynb](week3/day5/week3day5_mex_lab2.ipynb) | mine | Everything the project made on AWS, what it costs each day, and how to remove it | `labs/week3/monitor.py` |
