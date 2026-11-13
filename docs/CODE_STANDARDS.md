# Code standards

## The backend and the agent

- `backend/app/` holds the records and the API. `backend/agent/` holds the agent. `app` never imports LangGraph or LangChain; it imports from `agent` only the loaders in `agent.prompts` (the first instructions and the first recipe, which seed version 1).
- The agent reaches the backend only over HTTP, with the API key in `X-API-Key`, through one client, `agent/connection.py`. `agent/api.py` (the claim agent's bookkeeping, the worker, the dispatcher and the entrypoint) and `agent/improver_api.py` (the improver) are the only modules that call it, apart from the model's tools, which go by a route; `backend/app/` owns every record. The agent's one connection to the database is LangGraph's checkpoints.
- The model's tools and the payment reach the app and the context layer by one of two routes, one module each, chosen when a claim graph is built: `agent/direct.py` (the app with the key, the context layer over unsigned MCP) and `agent/gateway.py` (the Gateway's MCP tools, signed with SigV4). Both sit on `agent/mcp_bridge.py`, which runs each async MCP call on a loop of its own and never takes the main thread's loop away. The agent's bookkeeping always goes direct. The context layer (`backend/context/`) imports nothing from `app` or `agent`, and `app` and `agent` import nothing from `context`.
- The agent imports nothing from `app` or `context` (`tests/test_agent_boundaries.py` reads the imports, and imports `agent/` alone), so `runtime/` with `agent/` beside it is a package on its own. It has its own settings (`agent/settings.py`) with the app's names and defaults.
- The app keeps one piece of the agent's work going at a time (`app/work.py`): it creates the run, the round or the review, under one lock row, then asks the agent by id. A request uses one database connection at a time. The agent keeps no one-at-a-time rule of its own, tells the app every minute that it is working (`agent/heartbeat.py`), and when its work fails stops only that run or round.
- Our code, not the model, enforces the rules: the $1,000 limit, which claims a run works, attempts, and one decision and one payment per claim.
- The graph never applies the limit. It asks the backend whether a sign-off is needed, and the backend refuses a payment over the limit with a 403 (`pay_claim`) before it looks at the claim. A sign-off pays the claim in the backend.

## The six parts of the agent

1. **Prompts** (`prompts.py`, `instructions_*.md`, `recipe_v1.md`): what it is told. Never a claim id, and nothing from the handlers' decisions in `data/evals/`. Instructions are written as goals, what the agent is for and what good looks like, around the recipe where there is one; a list of steps to follow goes in the recipe, which a handler can change.
2. **Tools** (`tools.py`, and those in `queue.py`): what it can do. Customer text reaches the model only inside tool results. Tools take the claim from the graph's state, not from the model. A refusal is text returned to the model.
3. **The run loop**: the nodes of the claim agent's hand-written graph in `graph.py`, which record every step and its cost, return refusals to the model and allow at most 30 model calls.
4. **The entry points** (`entrypoint.py`, the AgentCore app, and `__main__.py`): `worker.py` says what working a claim means, whichever entry point asks; `worker.work` is the one way in.
6. **The improver** (`improver.py`, `improver_backend.py`, `improver_instructions.md`): an agent of its own whose four tools reach the records only through `improver_backend.py`. It proposes; code keeps or rolls back, and a handler decides.
5. **The dispatcher** (`dispatcher.py`): a graph with no model that works a run's claims, up to N at once, through `worker.work` only, so it could later point at the agent in the cloud. Each claim keeps its own thread, never a shared one or a subgraph, and the dispatcher saves no progress of its own.

## The Copilot

The Copilot is a part of its own, with the same rules as the others, in `backend/agent/copilot/`:

1. **Prompts** (`instructions.md`): its goal, who it works for, what it can see and do, the success criteria and how to answer. Written as goals; they do not script its steps. Never a claim id.
2. **Tools** (`tools.py`, `context_tools.py`): each reads who is asking from `ToolRuntime` and calls the app with that handler's sign-in. A refusal is text returned to the model; the app not answering is raised. The sign-off is checked in code before the pause.
3. **The loop** (`graph.py`): a hand-written graph; the pause is an `interrupt()` in a node of its own with nothing before it.
4. **The entry point** (`entrypoint.py`, `turns.py`): the AgentCore app, and the work of a turn. Nothing happens at import.
5. **The records it keeps** go through the app (`app_api.py`), never to the database; the app builds the confirmation card from its own records.

The Copilot imports nothing from `app`, never sends the agent's API key, and never saves, logs or echoes the handler's sign-in (`tests/test_copilot_graph.py` and `tests/test_copilot_routes.py` look for it).

## Clients

- Each outside service has one module that talks to it, and the rest of the code asks that module: the model through `agent/models.py` (`chat_model`), the judge through `app/judge.py`, Langfuse through `agent/tracing.py`, Supabase Auth's keys through `app/auth.py` and, in the dashboard, `auth/supabase.ts`.
- A client is made from settings and passed in, so a test hands in a stub. `conftest.py` makes `app.judge.make_client` raise, so a test that would reach a model fails.
- The claim agent's modules never import `app.scoring`, `app.improvement`, `app.judge`, `agent.improver` or `agent.improver_backend`, and `answer_key` is named only in `db.py`, `seed.py`, `scoring.py` and `judge.py`. Tests read the modules' imports to keep it so.

## The records

- SQLAlchemy Core, so each query is written once for SQLite and Postgres. Tables are defined in `app/db.py`.
- An operation takes the `Engine`. Never hold a connection across a model call, or inside another operation: each process has 4 at most, and up to 25 claim agents share them. They wait for a connection rather than fail.
- Every statement commits by itself. A trip to Supabase takes about 220 ms, and `BEGIN`, `COMMIT` and a ping per checkout would make each query four trips. `engine.begin()` therefore does not open a transaction here. A single statement, with its constraints, is atomic.
- So an operation must be safe if it stops between two statements, and when run again. A step can run again after a restart anyway: an operation that records something treats a repeat as done, and returns the claim. Something different is refused.
- Anything a handler decides is one conditional statement (`UPDATE ... WHERE still open`), never a read and then a write.
- `tests/test_round_trips.py` keeps the path from a sign-off to its payment short.
- A `DATABASE_URL` or `UNDERCOVER_AGENT` in a `.env` file means nothing on its own: they count only in the process environment.

## The API

Every operation has an `operation_id`, a one-line docstring and no optional inputs. `test_every_operation_of_the_app_follows_the_api_rules` checks the backend, and `test_no_operation_has_an_optional_input` the agent's payloads.

## Tests

- Backend behavior is tested on a stub model through the real graph, the real dispatcher and real SQLite files. Tests name what should happen.
- `tests/fakes.py` holds the stub model and helpers. `RawDb` lets a test arrange or inspect tables in plain SQL; production code does not use it.
- The front end has component tests with a mocked API. End-to-end tests run the whole app on the scripted model, with the real agent app on the scripted model on port 8003, behind a wrapper that writes down each run it is asked to work. The backend and the stand-in share a fixed API key.

## Style

Python 3.11+, type hints on public functions, short docstrings that say why. Comments only where the code cannot say it. Names of the product are MarvInsurance and the Marvin Agent. `undercover` (the schema) and `UNDERCOVER_AGENT` were chosen by IT and stay as they are.
