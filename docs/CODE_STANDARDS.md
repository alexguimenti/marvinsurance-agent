# Code standards

## The backend and the agent

- `backend/app/` holds the records and the API. `backend/agent/` holds the agent. `app` never imports LangGraph or LangChain; it imports from `agent` only the loaders in `agent.prompts` (the first instructions and the first recipe, which seed version 1).
- The agent reaches the backend only over HTTP, with the API key in `X-API-Key`, through one client, `agent/connection.py`. `agent/api.py` (the claim agent, the worker, the dispatcher and the service) and `agent/improver_api.py` (the improver) are the only modules that call it; `backend/app/` owns every record. The agent's one connection to the database is LangGraph's checkpoints.
- Our code, not the model, enforces the rules: the $1,000 limit, which claims a run works, attempts, and one decision and one payment per claim.
- The graph never applies the limit. It asks the backend whether a sign-off is needed, and the backend refuses a payment over the limit with a 403 (`pay_claim`) before it looks at the claim. A sign-off pays the claim in the backend.

## The six parts of the agent

1. **Prompts** (`prompts.py`, `instructions_*.md`, `recipe_v1.md`): what it is told. Never a claim id, and nothing from the handlers' decisions in `data/evals/`. Instructions are written as goals, what the agent is for and what good looks like, around the recipe where there is one; a list of steps to follow goes in the recipe, which a handler can change.
2. **Tools** (`tools.py`, and those in `queue.py`): what it can do. Customer text reaches the model only inside tool results. Tools take the claim from the graph's state, not from the model. A refusal is text returned to the model.
3. **The run loop**: the nodes of the claim agent's hand-written graph in `graph.py`, which record every step and its cost, return refusals to the model and allow at most 30 model calls.
4. **The service** (`service.py`, `__main__.py`): its entry points. `worker.py` says what working a claim means, whichever entry point asks; `worker.work` is the one way in.
6. **The improver** (`improver.py`, `improver_backend.py`, `improver_instructions.md`): an agent of its own whose four tools reach the records only through `improver_backend.py`. It proposes; code keeps or rolls back, and a handler decides.
5. **The dispatcher** (`dispatcher.py`): a graph with no model that works a run's claims, up to N at once, through `worker.work` only, so it could later point at the agent in the cloud. Each claim keeps its own thread, never a shared one or a subgraph, and the dispatcher saves no progress of its own.

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

Every operation has an `operation_id`, a one-line docstring and no optional inputs. `test_every_operation_follows_the_api_rules` checks the backend and the service.

## Tests

- Backend behavior is tested on a stub model through the real graph, the real dispatcher and real SQLite files. Tests name what should happen.
- `tests/fakes.py` holds the stub model and helpers. `RawDb` lets a test arrange or inspect tables in plain SQL; production code does not use it.
- The front end has component tests with a mocked API. End-to-end tests run the whole app on the scripted model, with a stand-in for the agent's service on port 8003 that writes down each start. The backend and the stand-in share a fixed API key.

## Style

Python 3.11+, type hints on public functions, short docstrings that say why. Comments only where the code cannot say it. Names of the product are MarvInsurance and the Marvin Agent. `undercover` (the schema) and `UNDERCOVER_AGENT` were chosen by IT and stay as they are.
