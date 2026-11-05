# Code standards

## The backend and the agent

- `backend/app/` holds the records and the API. `backend/agent/` holds the agent. `app` never imports LangGraph or LangChain; its one import from `agent` is `agent.prompts`, for the instructions page.
- The agent calls `app`'s operations in Python and has its own connections to the same database.
- Our code, not the model, enforces the rules: the $1,000 limit, the cap and scope of a run, attempts, and one decision and one payment per claim.
- The graph never applies the limit. It asks the backend (`needs_sign_off`) and the backend refuses a payout over the limit with no sign-off (`pay`).

## The four parts of the agent

1. **Prompts** (`prompts.py`, `instructions_*.md`): what it is told. Never a claim id, and nothing from the handlers' decisions in `data/evals/`.
2. **Tools** (`tools.py`, and those in `queue.py`): what it can do. Customer text reaches the model only inside tool results. Tools take the claim from the graph's state, not from the model. A refusal is text returned to the model.
3. **The run loop**: for the claim agent, the nodes of the hand-written graph in `graph.py`, which record every step, return refusals to the model and allow at most 30 model calls. For the queue agent, `create_agent` in `queue.py`.
4. **The service** (`service.py`, `__main__.py`): its entry points. `worker.py` says what working a claim means, whichever entry point asks.

## The records

- SQLAlchemy Core, so each query is written once for SQLite and Postgres. Tables are defined in `app/db.py`.
- An operation takes the `Engine`. Never hold a connection across a model call, or inside another operation: each process has very few.
- Every statement commits by itself. A trip to Supabase takes about 220 ms, and `BEGIN`, `COMMIT` and a ping per checkout would make each query four trips. `engine.begin()` therefore does not open a transaction here. A single statement, with its constraints, is atomic.
- So an operation must be safe if it stops between two statements, and when run again. A step can run again after a restart anyway: an operation that records something treats a repeat as done, and returns the claim. Something different is refused.
- `tests/test_round_trips.py` keeps the path from a sign-off to its payment short.
- A `DATABASE_URL` or `UNDERCOVER_AGENT` in a `.env` file means nothing on its own: they count only in the process environment.

## The API

Every operation has an `operation_id`, a one-line docstring and no optional inputs. `test_every_operation_follows_the_api_rules` checks the backend and the service.

## Tests

- Backend behavior is tested on a stub model through the real graph and real SQLite files. Tests name what should happen.
- `tests/fakes.py` holds the stub model and helpers. `RawDb` lets a test arrange or inspect tables in plain SQL; production code does not use it.
- The front end has component tests with a mocked API. End-to-end tests run the whole app on the scripted model, with a stand-in for the agent's service on port 8003.

## Style

Python 3.11+, type hints on public functions, short docstrings that say why. Comments only where the code cannot say it. Names of the product are MarvInsurance and the Marvin Agent. `undercover` (the schema) and `UNDERCOVER_AGENT` were chosen by IT and stay as they are.
