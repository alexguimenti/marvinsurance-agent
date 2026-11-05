# A swarm of claim agents and a scoreboard: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Tick them off here.

**Goal:** A dispatcher with no model works the whole queue with up to N claim agents at once, puts problems right without stopping, records the cost and time of every claim with its run, and runs evaluations whose measures show on a new Scoreboard page.

**Architecture:** `agent/dispatcher.py` is a LangGraph graph with no model: `list_claims` fans out one `Send` per claim to `work_claim`, with `max_concurrency` set to N. `work_claim` calls one function of the harness, `worker.work`, which carries on or starts the claim on its own thread and its own checkpointer, over the process's shared pool. Runs, their claims, cost, time, the answer key and the scores are records in `backend/app/`. The service starts dispatches and evaluations when the backend asks it.

**Tech Stack:** FastAPI, SQLAlchemy Core, SQLite (tests, e2e) and Supabase Postgres, LangGraph (`Send`, `RetryPolicy`, `error_handler`, `PostgresSaver`, `SqliteSaver`), LangChain, OpenRouter, Langfuse, React, Vitest, Playwright.

**Spec:** `docs/SWARM.md`. Read it alongside this plan. Where they disagree, the spec wins unless a line under "Decisions" says otherwise.

**Sidenote from the brief:** For consistency with the course, please read `labs/week2day4.ipynb` and build the dispatcher in the same style as the lab. Where the lab and this brief differ, follow the brief. In this repo the lab is `labs/week2day4_mex.ipynb`.

## Decisions

The brief was written against a slightly different repo. The owner chose the default model and left every other decision to the plan, on 2026-11-05.

| The brief says | This repo | Decision |
|---|---|---|
| `backend/agent_service.py` | `backend/app/agent_client.py` | Keep ours. Its `start_run` becomes `start(http, url, path, body)` |
| `backend/instructions/queue.md` | `backend/agent/instructions_run.md` | That file goes, with `load_instructions("run")` |
| The to-do list, `write_todos` | Our tools are `set_todo` and `update_todo`, table `todo_items`, `TodoPanel`, `e2e/tests/00-harness.spec.ts` | All of them go. The table `run_events` goes too: only the queue agent wrote to it |
| `e2e/run.spec.ts` | `e2e/tests/00-harness.spec.ts` | Replaced by `00-dispatch.spec.ts` and `03-scoreboard.spec.ts` |
| `npm run seed` | `npm run reseed` | Keep `npm run reseed`, and add `npm run seed` as the same command |
| `MODEL_NAME` | `AGENT_MODEL`, and `model` in `agent.toml` | `MODEL_NAME` in the environment chooses the model, as the brief says. `AGENT_MODEL` goes. The reasoning effort is `low` for the default model and none for any other, so `AGENT_REASONING_EFFORT` and `reasoning_effort` in `agent.toml` go |
| GPT-6 Luna (`openai/gpt-6-luna`) by default | `deepseek/deepseek-v4.1-flash` with low effort | Owner: no Luna. The default stays DeepSeek V4.1 Flash with low reasoning effort. The rest of point 8 stands: `MODEL_NAME` chooses another model, which runs with no effort, recorded as none |
| `docs/CODE_STANDARDS.md` rules 10 and 13 | Our standards are not numbered | Update "The four parts of the agent" (now five) and the connections rule under "The records" |
| SPEC sections on working the queue and on what the team needs to see | Sections 5, 6 and 7 | Update those. Sections 3.3 and 4, the Rules, stay as they are |
| The service's reloader, `lsof -ti tcp:8002` | The service runs without `--reload`; Windows | Kill every process on 8002 with `Get-NetTCPConnection -LocalPort 8002 -State Listen` and `Stop-Process -Force` |

These follow from the brief and the code:

1. The second try after an ordinary error happens straight away, in the same worker, as soon as the claim is back in the queue. The other way is to send it again after every claim of the dispatch has finished, as the queue agent did: the run would then take up to twice its slowest claim.
2. The heartbeat and the adoption of a stopped run go. The service marks a running run as stopped when it starts, as the brief says, and the next dispatch picks up the stopped run's claims in progress. Only the service starts runs now, so a run can stop only if the service stops.
3. The queue page loses its tick boxes and its Limit field: a dispatch works every claim in progress and every waiting one. N is the only choice.
4. `scripts/score.py` and `test_score.py` go. Evaluations replace them, and two ways of scoring would drift apart.
5. A copy's saved progress is cleared once it stops being worked: decided, held for sign-off or left for a handler. The brief asks it for held copies; clearing every finished copy keeps the checkpoint tables small, and nothing ever carries a copy on.
6. A copy's id is `E<run_id>-<claim_id>`, for example `E7-CLM-1003`. It is written into the copy's `payload` too. The scripted model reads the original id from it, so the e2e runs behave the same on copies.
7. The set of claims an evaluation copies comes from a table of its own, `eval_sets`, which holds which claim belongs to which set and whether it is `tiny`. The handlers' decisions are in `answer_key`, which only `app/scoring.py` reads. So the service builds an evaluation without reading a decision.
8. The reasoning effort is stored on the run as `"none"` when none was sent.
9. A claim's time is the sum of its graph runs in `worker`, each from the call to its return. A pause for a sign-off ends a graph run, so the wait is left out.

Checked on LangGraph 1.2.19 with a throwaway graph and no model, on 2026-11-05. `Send` from a node to a worker with `max_concurrency=3` never had more than 3 workers at once. A `RetryPolicy` with `retry_on=(ModelRateLimitError, ModelTimeoutError)` ran a worker that kept raising `ModelRateLimitError` 3 times and a `ValueError` once. `error_handler(task, error: NodeError)` received the `Send` payload as `task`, and its return value joined the state, so the graph finished. `langchain_core.exceptions` has both errors, and `langchain_openai` raises subclasses of them for OpenAI's 429 and timeout. `PostgresSaver` and `SqliteSaver` each take a `threading.Lock` around every read and write.

## Global Constraints

- "Code chooses the claims and keeps track of the run, with no model."
- "Only one dispatch or evaluation runs at a time, and a second start is refused while one is running."
- "Keep each process at 4 at most": backend 4; the agent's service 2 for records and 2 for checkpoints. Claim agents wait for a connection rather than fail.
- "Each claim keeps its own thread, as it does today, never a shared thread or a subgraph."
- "The dispatcher saves no progress of its own."
- "No claim is ever decided or paid twice"; the sign-off rule and the $1,000 limit stay as they are.
- N is from 1 to 25, with 3 to start with.
- Every API operation has an `operation_id`, a one-line docstring and no optional inputs.
- Tests never call a model. `npm run check` is green with `.env` moved away.
- `data/evals/` is read only by seeding. Nothing in `backend/agent/` reads it or the `answer_key` table.
- Everything `npm run dev` starts works on Windows.
- Live runs: reseed once first, then only the runs in "What done looks like".

## Review Focus

1. Two claims of one dispatch hit a rate limit together, at N=25, on Supabase: each carries on as its own attempt and neither waits for the other's saver. Test in Task 3 (two savers), Task 5 (retry).
2. A sign-off arrives while a dispatch is working other claims: the service pays it within five seconds, and the dispatch does not also take that claim. Test in Task 5.
3. The service is killed during an evaluation: the evaluation shows as stopped, its copies never reach a dispatch, and the queue's counts do not change. Test in Task 8.
4. A claim fails on the first try and hits a rate limit on the second: it stays in progress, with its second attempt counted once. Test in Task 5.
5. The connection pool is empty when 25 claim agents start together: they wait, and none fails. Test in Task 3.

---

## File structure

```
backend/app/       db.py (new tables and columns), runs.py (dispatches and evaluations), usage.py (cost and time),
                   evaluations.py (copies, answer key), scoring.py (the measures), seed.py, payments.py, claims.py,
                   api.py, agent_client.py, config.py
backend/agent/     dispatcher.py      the fifth part: works a run's claims, N at once
                   worker.py          work(env, claim_id, run_id): the one function the dispatcher calls
                   graph.py           records each model call's cost
                   checkpoints.py     a saver for each claim run, on one pool
                   service.py, __main__.py, tracing.py, models.py, scripted.py
                   gone: queue.py, instructions_run.md
frontend/src/      components/RunPanel.tsx (replaces TodoPanel), pages/Scoreboard.tsx, pages/Evaluation.tsx
e2e/               stand_in_agent.py records each start; tests/00-dispatch.spec.ts, tests/03-scoreboard.spec.ts
```

---

### Task 1: The default model and `MODEL_NAME`

**Files:** Modify `backend/app/config.py`, `backend/agent/models.py`, `agent.toml`, `package.json` (`seed`). Test `backend/tests/test_config.py`.

**Interfaces:**
- Produces: `config.DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"`, `config.DEFAULT_EFFORT = "low"`. `Settings.agent_model` comes from `MODEL_NAME` in the environment, else `model` in `agent.toml`, else `DEFAULT_MODEL`. `Settings.reasoning_effort -> str | None` (a property): `"low"` for the default model, `None` for any other. `chat_model` sends `reasoning` only when it is set.

- [ ] **Step 1: Write the failing tests**

```python
def test_default_model_is_deepseek_with_low_effort(monkeypatch):
    monkeypatch.delenv("MODEL_NAME", raising=False)
    settings = Settings()
    assert settings.agent_model == "deepseek/deepseek-v4.1-flash" and settings.reasoning_effort == "low"

def test_model_name_chooses_another_model_with_no_effort(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "anthropic/claude-haiku-5-5")
    settings = Settings()
    assert settings.agent_model == "anthropic/claude-haiku-5-5" and settings.reasoning_effort is None

def test_another_model_sends_no_reasoning(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "anthropic/claude-haiku-5-5")
    assert "reasoning" not in chat_model(Settings()).extra_body
```

- [ ] **Step 2: Run them.** `cd backend; .venv/Scripts/python -m pytest tests/test_config.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** `MODEL_NAME` is read in `model_post_init` like `DATABASE_URL`, so the precedence stays: environment, then `agent.toml`. Remove `AGENT_MODEL` and `AGENT_REASONING_EFFORT` from the docs and the comment in `agent.toml`. Add `"seed": "node scripts/reseed.mjs"` to the root `package.json`.
- [ ] **Step 4: Run all backend tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(agent): MODEL_NAME chooses the model; only the default runs with low reasoning effort`.

### Task 2: Cost and time for every claim, with its run

**Files:** Create `backend/app/usage.py`. Modify `backend/app/db.py`, `backend/agent/graph.py`, `backend/agent/worker.py`, `backend/agent/events.py`, `backend/app/api.py` (`/claims/{id}` usage), `backend/app/runs.py` (drop `run_usage`, `sum_usage`). Test `backend/tests/test_usage.py`, `test_graph.py`, `test_worker.py`.

**Interfaces:**
- Tables: `model_calls(id, claim_id, run_id NULL, model, input_tokens, output_tokens, reasoning_tokens, cost_usd NULL, created_at)` and `work_spans(id, claim_id, run_id NULL, started_at, seconds)`.
- Produces in `app/usage.py`: `record_model_call(engine, claim_id: str, run_id: int | None, model: str, usage: dict) -> None`; `record_work_time(engine, claim_id: str, run_id: int | None, started_at: datetime, seconds: float) -> None`; `claim_totals(engine, claim_id: str) -> dict` and `run_totals(engine, run_id: int) -> dict`, both `{"calls", "input_tokens", "output_tokens", "reasoning_tokens", "cost_usd", "seconds"}`; `per_claim(engine, run_id: int) -> dict[str, dict]` with the same keys.
- Produces in `agent/events.py`: `call_usage(message: AIMessage) -> dict`, one reply's tokens and OpenRouter's `token_usage.cost`. `Usage` stays for the state's running total.
- The run reaches the graph as `config["configurable"]["run_id"]` (`None` outside a run). `model_node` calls `record_model_call` after every reply, failed attempts included. `decide` no longer writes a `usage` trace event, and `_fail` no longer does.
- `AgentEnv.clock: Callable[[], float] = time.monotonic`, so a test can time a claim without sleeping. `_drive` times each `invoke` and records it with `record_work_time`.
- `/claims/{id}` takes `usage` from `claim_totals`, now with `seconds`.

- [ ] **Step 1: Write the failing tests**

```python
def test_each_model_call_is_recorded_with_its_run(env):
    work_claim(env, "CLM-1001", run_id=7)
    calls = model_calls(env, "CLM-1001")
    assert len(calls) == env.model.calls and {c["run_id"] for c in calls} == {7}
    assert run_totals(env.engine, 7)["cost_usd"] == pytest.approx(sum(c["cost_usd"] for c in calls))

def test_a_failed_attempt_counts_in_its_runs_cost(env): ...      # stub raises after one call; the call is recorded
def test_work_outside_a_run_is_recorded_with_no_run(env): ...    # carry_on after a sign-off: run_id is None
def test_time_leaves_out_the_wait_for_a_sign_off(env):
    env.clock = FakeClock(step=2.0)          # every reading moves on 2 s
    work_claim(env, "CLM-1003", run_id=7)    # pauses for sign-off: one graph run
    env.clock.jump(86_400)                   # a day waiting for the handler
    sign_off(env.engine, env.ref, "PAY-CLM-1003", "HND-01")
    carry_on(env, "CLM-1003")                # the second graph run, with no run
    assert claim_totals(env.engine, "CLM-1003")["seconds"] == 4.0
    assert run_totals(env.engine, 7)["seconds"] == 2.0
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** `FakeClock` goes in `tests/fakes.py`. The stub model's replies carry `token_usage.cost`, so the cost is not zero.
- [ ] **Step 4: Run all backend tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(usage): record each model call's cost and each claim's time with its run`.

### Task 3: A saver for each claim run, and connections that wait

**Files:** Modify `backend/agent/checkpoints.py`, `backend/agent/worker.py` (`AgentEnv`, `open_env`, `carry_on`, `_fail`), `backend/app/db.py` (`make_engine`), `backend/agent/service.py`, and the tests that use `env.graph`. Test `backend/tests/test_checkpoints.py`, `test_db.py`, `test_worker.py`.

**Interfaces:**
- `checkpoints.make_checkpoints(settings) -> context manager yielding Checkpoints`. `Checkpoints.saver() -> context manager yielding a new saver`: on Postgres a new `PostgresSaver` over the one `ConnectionPool` (2 connections, `timeout=CONNECTION_WAIT_S`); on SQLite a `SqliteSaver` on its own `sqlite3` connection to the same file. `setup()` and row level security run once, in `make_checkpoints`.
- `CONNECTION_WAIT_S = 600` in `app/db.py`. `make_engine` passes it as `pool_timeout`, so a claim agent waits for a connection rather than fail.
- `AgentEnv` loses `graph` and gains `checkpoints` and `model`. `AgentEnv.claim_graph() -> context manager yielding a compiled claim graph` on a saver of its own. `invoke`, `carry_on`'s `get_state` and `_fail`'s state read all use the graph of the one claim run.
- Tests get `tests/fakes.claim_state(env, thread_id)` in place of `env.graph.get_state(...)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_each_claim_run_gets_a_saver_of_its_own(env):
    with env.claim_graph() as a, env.claim_graph() as b:
        assert a.checkpointer is not b.checkpointer

def test_two_claims_worked_at_once_both_finish(env):
    env.model = SlowStubModel(seconds=0.2)              # two threads, each its own saver
    with ThreadPoolExecutor(2) as pool:
        lines = list(pool.map(lambda c: work_claim(env, c), ["CLM-1001", "CLM-1002"]))
    assert all(": approve" in line or ": decline" in line or ": refer" in line for line in lines)

def test_a_claim_agent_waits_for_a_connection_rather_than_fail(db_path):
    engine = make_engine(Settings(db_path=db_path), max_connections=1)
    with ThreadPoolExecutor(5) as pool:                 # five at once on one connection
        assert list(pool.map(lambda _: busy_query(engine, hold_s=0.1), range(5))) == [1] * 5

def test_make_engine_waits_for_a_connection(db_path):
    assert make_engine(Settings(db_path=db_path), max_connections=4).pool._timeout == 600
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Port every test that reads `env.graph` to `claim_state`. `test_round_trips.py` keeps its count of checkpoints per sign-off.
- [ ] **Step 4: Run all backend tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(checkpoints): a saver for each claim run on one pool; connections wait`.

### Task 4: The harness function

**Files:** Modify `backend/agent/worker.py`, `backend/app/runs.py` (remove `todo_for_claim`). Test `backend/tests/test_worker.py`.

**Interfaces:**
- Produces: `worker.work(env, claim_id: str, run_id: int | None) -> str`. The one function the dispatcher calls. A claim in progress with a thread is carried on (`carry_on`); a waiting claim is started (`work_claim`); any other claim is reported and left. It returns the one-line report of today.
- `worker.RETRYABLE = (ModelRateLimitError, ModelTimeoutError)`, from `langchain_core.exceptions`. `_drive` lets them through without settling the claim: no attempt is counted, the claim stays in progress on its thread, and a trace event says it will carry on.
- `work_claim` and `carry_on` stay as they are otherwise, minus their calls to the to-do list.

- [ ] **Step 1: Write the failing tests**

```python
def test_work_starts_a_waiting_claim(env): ...
def test_work_carries_on_a_claim_in_progress_as_the_same_attempt(env): ...
def test_a_rate_limit_leaves_the_claim_in_progress_on_its_thread(env):
    env.model = StubModel(raise_on_call={2: ModelRateLimitError("429")})
    with pytest.raises(ModelRateLimitError):
        work(env, "CLM-1001", 7)
    row = claim(env, "CLM-1001")
    assert row["status"] == "in_progress" and row["attempts"] == 0 and row["thread_id"] == "CLM-1001-1"

def test_a_timeout_is_let_through_the_same_way(env): ...         # ModelTimeoutError
def test_any_other_error_still_fails_the_attempt(env): ...       # RuntimeError: back to waiting, attempts 1
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run all backend tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(worker): one function to work a claim; rate limits and timeouts leave it in progress`.

### Task 5: The dispatcher in place of the queue agent

**Files:** Create `backend/agent/dispatcher.py`. Delete `backend/agent/queue.py`, `backend/agent/instructions_run.md`, `backend/tests/test_queue.py`. Modify `backend/app/db.py`, `backend/app/runs.py`, `backend/agent/scripted.py` (remove `ScriptedQueueAgent`), `backend/agent/prompts.py`, `backend/agent/tracing.py`, `backend/agent/__main__.py`, `backend/app/seed.py`, `scripts/reset_db.py`. Test `backend/tests/test_dispatcher.py`, `test_runs.py`, `test_cli.py`, `test_tracing.py`.

**Interfaces:**
- `runs` table: `run_id, kind ('dispatch' | 'evaluation'), status ('running' | 'finished' | 'stopped'), workers, model, reasoning_effort, eval_set NULL, tiny NULL, error NULL, started_at, finished_at`. The `one_running_run` index stays, so one run of either kind at a time holds across processes. `cap`, `scope`, `summary`, `usage` and `heartbeat_at` go.
- New table `run_claims(run_id, claim_id, position, started_at NULL, finished_at NULL)`, primary key `(run_id, claim_id)`. New column `claims.submitted_on`, filled by seeding.
- Produces in `app/runs.py`: `start_dispatch(engine, workers: int, model: str, effort: str) -> int` (raises `RunActive`); `running_run(engine) -> row | None`; `claims_to_dispatch(engine) -> list[str]` (in progress with a thread, then waiting, both by `submitted_on` then id, copies left out); `add_run_claims(engine, run_id, claim_ids) -> None`; `claim_started(engine, run_id, claim_id) -> None`; `claim_finished(engine, run_id, claim_id) -> None`; `finish_run(engine, run_id) -> None`; `stop_running(engine) -> list[int]`; `run_claims_view(engine, run_id) -> list[dict]` (each claim with its status, decision, payout, payment status, `started_at`, `finished_at`, cost and seconds); `run_summary(engine, run_id) -> dict` with `approved`, `declined`, `referred`, `awaiting_sign_off` (`[{claim_id, payout}]`), `left_for_handler` and `in_progress` (lists of ids), from the records only.
- Produces in `agent/dispatcher.py`, in the lab's style:

```python
class DispatchState(BaseModel):
    run_id: int
    outcomes: Annotated[list[dict], operator.add] = Field(default_factory=list)  # the reducer joins the workers' lines

class ClaimTask(TypedDict):
    claim_id: str
    run_id: int

CLAIM_RETRY = RetryPolicy(initial_interval=5, backoff_factor=2, max_interval=60, max_attempts=4, retry_on=RETRYABLE)
def build_dispatcher(env: AgentEnv, retry: RetryPolicy) -> CompiledStateGraph: ...
def dispatch(env: AgentEnv, run_id: int, workers: int, retry: RetryPolicy = CLAIM_RETRY) -> None: ...  # max_concurrency=workers, then finish_run
```

  `list_claims` lists the run's claims (`claims_to_dispatch` for a dispatch, without claims `env.working` already holds), writes them with `add_run_claims` and returns `Command(goto=[Send("work_claim", ClaimTask(...)) ...])`. `work_claim` calls `claim_started`, then `worker.work`; if the claim is back in the queue after an ordinary error, it calls `worker.work` once more (Decision 1); then `claim_finished`. Its `error_handler`, `on_claim_error(task: ClaimTask, error: NodeError) -> dict`, records a trace event that the claim stays in progress for the next dispatch, calls `claim_finished` and returns the outcome. The graph is compiled with no checkpointer.
- Tracing: `trace_config(claim_id, thread_id, model, run_id: int | None)` adds the tag `f"run-{run_id}"` to `langfuse_tags` when there is a run.
- `uv run -m agent CLM-1001`: one positional claim id, worked in this process with `worker.work(env, claim_id, None)`. `--limit` and `--claims` go.
- Seeding on Supabase drops `todo_items` and `run_events` if they are there (`DROP TABLE IF EXISTS`), because they are no longer in `metadata`.

- [ ] **Step 1: Write the failing tests on a stub model**

```python
def test_never_more_than_n_claims_at_once(env):
    env.model = CountingStubModel(seconds=0.05)
    run_id = start_dispatch(env.engine, 3, "stub", "low")
    dispatch(env, run_id, workers=3)
    assert env.model.peak_claims == 3
    assert {r["claim_id"] for r in run_claims_view(env.engine, run_id)} == set(original_ids(env))

def test_oldest_submitted_first_and_claims_in_progress_first(env): ...
def test_a_rate_limit_carries_on_as_the_same_attempt_with_one_decision(env):
    env.model = StubModel(raise_on_call={("CLM-1001", 2): ModelRateLimitError("429")})
    dispatch(env, start_dispatch(env.engine, 3, "stub", "low"), 3, retry=FAST_RETRY)
    row = claim(env, "CLM-1001")
    assert row["attempts"] == 0 and row["thread_id"] == "CLM-1001-1" and count(env, "decisions", "CLM-1001") == 1

def test_retries_run_out_and_the_claim_waits_for_the_next_dispatch(env):
    env.model = StubModel(always_raise={"CLM-1001": ModelRateLimitError("429")})
    run_id = start_dispatch(env.engine, 3, "stub", "low")
    dispatch(env, run_id, 3, retry=FAST_RETRY)
    assert claim(env, "CLM-1001")["status"] == "in_progress"
    assert get_run(env.engine, run_id)["status"] == "finished"
    assert run_summary(env.engine, run_id)["in_progress"] == ["CLM-1001"]

def test_a_failed_claim_is_tried_once_more_in_the_same_dispatch(env): ...   # fails once: decided, attempts 1
def test_a_claim_that_fails_twice_is_left_for_a_handler(env): ...
def test_one_bad_claim_never_stops_the_dispatch(env): ...                   # an error in the worker itself
def test_a_claim_being_resumed_is_skipped(env): ...                         # inside env.working("CLM-1003")
def test_a_rate_limit_on_the_second_try_counts_the_attempt_once(env): ...   # Review Focus 4
def test_a_second_start_is_refused_while_one_runs(env):
    start_dispatch(env.engine, 3, "stub", "low")
    with pytest.raises(RunActive):
        start_dispatch(env.engine, 3, "stub", "low")

def test_the_summary_is_built_from_the_records(scripted_env):   # the real graph on ScriptedChatModel
    env = scripted_env
    run_id = start_dispatch(env.engine, 25, "scripted", "none")
    dispatch(env, run_id, 25)
    s = run_summary(env.engine, run_id)
    assert (s["approved"], s["declined"], s["referred"]) == counts_in_decisions_table(env)
    assert [p["claim_id"] for p in s["awaiting_sign_off"]] == ["CLM-1003", "CLM-1008", "CLM-1011"]
    assert s["left_for_handler"] == ["CLM-1013"]

def test_each_claims_trace_carries_its_runs_tag(): ...
def test_stop_running_marks_a_run_stopped(env): ...
def test_cli_works_one_claim(cli_env): ...
def test_cli_without_a_claim_id_is_refused(cli_env): ...
```

  `FAST_RETRY` is `CLAIM_RETRY` with `initial_interval=0.01` and `jitter=False`.

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Remove `begin_run`, `work_run`, the heartbeat, `missed_claims`, `taken_claims`, the to-do functions and `run_event(s)`. `test_evals_isolation.py` must still scan `backend/agent/`, now without `instructions_run.md`.
- [ ] **Step 4: Run all backend tests.** `grep -rn "todo\|queue_agent\|instructions_run" backend` returns nothing. Expected: PASS, and empty.
- [ ] **Step 5: Commit** `feat(dispatcher): a graph with no model works the queue N claims at once`.

### Task 6: Starting a dispatch through the service

**Files:** Modify `backend/agent/service.py`, `backend/app/agent_client.py`, `backend/app/api.py`. Test `backend/tests/test_service.py`, `test_api.py`.

**Interfaces:**
- Service: `POST /dispatches` with `{workers: int}` (1 to 25; `operation_id="start_dispatch"`), 202 `{"status": "started"}`. A busy flag (a `threading.Lock` taken without waiting) and `runs.running_run` are checked before answering: a run going is a 409. The run is created in the background, then `dispatch` runs; the flag is let go when it ends, however it ends. `POST /runs` and `POST /runs/selected` go.
- On startup the service calls `runs.stop_running`, then sweeps signed-off claims as today.
- Backend: `POST /dispatches` with `{workers: int}` (`start_dispatch`) forwards through `agent_client.start(http, url, "/dispatches", body)`: 503 `"the agent is not running"` when it is down, the service's 409 as a 409. `GET /dispatches/latest` (`get_latest_dispatch`) returns `{"run": ..., "claims": run_claims_view, "summary": run_summary or None while running, "totals": run_totals}`. `GET /instructions` returns `{"claim": ...}`. `/runs`, `/runs/selected`, `/runs/latest` and `/runs/{id}` go.
- `test_every_operation_follows_the_api_rules` covers the new routes of both apps.

- [ ] **Step 1: Write the failing tests**

```python
def test_service_starts_a_dispatch_with_n(service_env): ...
def test_service_refuses_a_second_start_while_one_runs(service_env): ...      # 409
def test_service_start_marks_a_running_run_stopped(service_env): ...
def test_a_sign_off_during_a_dispatch_is_paid_and_not_taken_twice(service_env): ...   # Review Focus 2
def test_backend_forwards_n_to_the_service(client, fake_service):
    assert client.post("/dispatches", json={"workers": 3}).status_code == 202
    assert fake_service.calls == [("/dispatches", {"workers": 3})]

def test_workers_outside_1_to_25_are_refused(client): ...                     # 0 and 26: 422
def test_a_dispatch_with_no_agent_is_503(client): ...
def test_latest_dispatch_shows_its_claims_summary_and_totals(client, dispatched): ...
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run all backend tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(service): start a dispatch with N claim agents; one run at a time`.

### Task 7: The dashboard: N, the run panel, and the end-to-end test

**Files:** Create `frontend/src/components/RunPanel.tsx`, `RunPanel.test.tsx`, `e2e/tests/00-dispatch.spec.ts`. Delete `TodoPanel.tsx`, `TodoPanel.test.tsx`, `e2e/tests/00-harness.spec.ts`. Modify `frontend/src/api.ts`, `format.ts`, `pages/Queue.tsx`, `pages/Queue.test.tsx`, `pages/Instructions.tsx`, `pages/Instructions.test.tsx`, `e2e/stand_in_agent.py`, `e2e/tests/01-queue.spec.ts`.

**Interfaces:**
- `api.ts`: `startDispatch(workers: number)`, `getLatestDispatch(): Promise<LatestDispatch>` with `run`, `claims: RunClaim[]`, `summary: RunSummary | null`, `totals: Usage & { seconds: number }`. `startRun`, `getLatestRun`, `TodoItem` go.
- Queue page: a number field labelled **Claim agents at once** (1 to 25, 3 to start with) and a **Start dispatch** button, disabled while a run is running. The tick boxes and **Limit** go. A 503 says the agent is not running; a 409 says a run is already going.
- `RunPanel`: the dispatch's claims, each linked to its page with its status and start time; its model cost and time; when it finishes, the summary: counts approved, declined and referred, each payout waiting for a sign-off, and each claim left for a handler or still in progress.
- Instructions page: the claim agent's instructions only.
- `stand_in_agent.py` writes down the body of every `POST /dispatches` and `POST /evaluations` before passing it to the real scripted service. `GET /received` returns `{"resumes": [claim ids], "starts": [{"path", "body"}]}`; `02-approval.spec.ts` reads `resumes`.

- [ ] **Step 1: Write the failing tests**: `Queue` "starts a dispatch with the N chosen, 3 to start with"; "says the agent is not running on a 503"; "says a run is going on a 409"; `RunPanel` "shows each claim of the dispatch and where it stands"; "shows the summary and the model cost and time when it finishes". E2e:

```ts
test('a dispatch started on the dashboard reaches the agent service with N, and works every claim', async ({ page, request }) => {
  await page.goto('/')
  await expect(page.getByLabel('Claim agents at once')).toHaveValue('3')
  await page.getByLabel('Claim agents at once').fill('5')
  await page.getByRole('button', { name: 'Start dispatch' }).click()
  await expect.poll(async () => (await (await request.get('http://127.0.0.1:8003/received')).json()).starts)
    .toContainEqual({ path: '/dispatches', body: { workers: 5 } })
  await expect(page.locator('.run-summary')).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('.run-summary')).toContainText('CLM-1003')      // waiting for a sign-off
  await expect(page.locator('.run-summary')).toContainText('CLM-1013')      // left for a handler
})
```

- [ ] **Step 2: Run them.** `cd frontend; npx vitest run`, then `cd e2e; npm test`. Expected: FAIL.
- [ ] **Step 3: Implement**, reusing the existing styles. `01-queue.spec.ts` and `02-approval.spec.ts` now run after a dispatch of the whole queue: update what they expect.
- [ ] **Step 4: Run `npm run check`** with `.env` moved aside; put it back. Expected: green.
- [ ] **Step 5: Commit** `feat(frontend): start a dispatch with N; the run panel shows its claims and summary`.

### Task 8: Evaluations on copies

**Files:** Create `backend/app/evaluations.py`. Modify `backend/app/db.py`, `backend/app/seed.py`, `backend/app/runs.py`, `backend/app/payments.py`, `backend/app/api.py` (`/claims`, `/approvals`, `/claims/{id}`), `backend/agent/worker.py`, `backend/agent/dispatcher.py`, `backend/agent/service.py`, `backend/agent/scripted.py`, `backend/tests/test_evals_isolation.py`. Test `backend/tests/test_evaluations.py`, `test_payments.py`, `test_api.py`.

**Interfaces:**
- Tables filled by seeding from `data/evals/`: `held_out_claims(claim_id, payload)`; `eval_sets(eval_set, claim_id, tiny)` for `evaluation` (the 25 queue claims) and `held_out`; `answer_key(eval_set, claim_id, decision, payout, reason)`. New columns `claims.copy_of` (the original id, `NULL` for the queue's claims) and `claims.evaluation_id`.
- Produces in `app/evaluations.py`: `EVAL_SETS = ("evaluation", "held_out")`; `start_evaluation(engine, ref, eval_set: str, tiny: bool, workers: int, model: str, effort: str) -> int`, which creates the run and its copies (`E<run_id>-<claim_id>`, waiting, the new id in the payload too) and adds them to `run_claims`; `is_copy(engine, claim_id) -> bool`. It reads `eval_sets` and `held_out_claims`, never `answer_key`.
- Copies are left out of `/claims`, `/approvals`, `claims_to_dispatch`, `stopped_claims` and `signed_off_claims`. `sign_off` on a copy raises `PermissionError("an evaluation's copy cannot be signed off")`, a 403.
- `worker.work` on a copy: once the claim agent has decided it, held it for sign-off or left it for a handler, the copy's thread is deleted from the checkpoints (`saver.delete_thread`). A held copy counts as finished.
- `/claims/{id}` adds `copy_of` and `evaluation_id`. `ClaimDetail` shows "Evaluation copy of CLM-1003, evaluation 7" and no notice to sign off.
- Service: `POST /evaluations` with `{eval_set: "evaluation" | "held_out", tiny: bool, workers: int}` (`start_evaluation`), the same busy rule as a dispatch. Backend: `POST /evaluations` (`start_evaluation`) forwards it.
- `ScriptedChatModel` reads the original id from `E<run>-<id>`, so copies get the scripted decisions.
- `test_evals_isolation.py`: `backend/agent/` and the prompts never name `data/evals` or `answer_key`; in `backend/app/` only `seed.py` names `data/evals` and only `scoring.py` reads `answer_key`.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_evaluation_leaves_the_queue_as_it_was(env):
    before = queue_counts(env)
    run_id = start_evaluation(env.engine, env.ref, "evaluation", False, 5, "scripted", "none")
    dispatch(env, run_id, 5)
    assert queue_counts(env) == before

def test_held_copies_count_as_finished_and_their_progress_is_cleared(env):
    run_id = start_evaluation(env.engine, env.ref, "evaluation", False, 5, "scripted", "none")
    dispatch(env, run_id, 5)
    held = claim(env, f"E{run_id}-CLM-1003")
    assert held["status"] == "awaiting_approval" and claim_state(env, held["thread_id"]).values == {}
    assert get_run(env.engine, run_id)["status"] == "finished"

def test_tiny_takes_the_five_tiny_claims_of_the_set(env): ...
def test_held_out_copies_come_from_the_held_out_claims(env): ...
def test_get_claim_on_a_copy_holds_nothing_from_the_handlers_decisions(env):
    run_id = start_evaluation(env.engine, env.ref, "evaluation", False, 5, "stub", "low")
    copy = get_claim(env.engine, f"E{run_id}-CLM-1001")
    assert copy["decision"] is None and copy["copy_of"] == "CLM-1001"
    assert not {"handler_id", "reason", "failure_class", "tests", "needs"} & all_keys(copy)  # answer-key fields
def test_a_sign_off_is_refused_on_a_copy(env): ...
def test_a_dispatch_never_carries_on_a_copy(env): ...
def test_a_stopped_evaluation_shows_as_stopped(env): ...          # Review Focus 3
def test_an_evaluation_records_its_set_n_model_and_effort(env): ...
def test_service_starts_an_evaluation_and_refuses_one_during_a_dispatch(service_env): ...
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run all backend tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(evaluations): work copies of a labelled set through the dispatcher`.

### Task 9: The scoreboard

**Files:** Create `backend/app/scoring.py`, `frontend/src/pages/Scoreboard.tsx`, `Scoreboard.test.tsx`, `pages/Evaluation.tsx`, `Evaluation.test.tsx`, `e2e/tests/03-scoreboard.spec.ts`. Delete `scripts/score.py`, `backend/tests/test_score.py`. Modify `backend/app/api.py`, `frontend/src/api.ts`, `App.tsx`, `format.ts`. Test `backend/tests/test_scoring.py`, `test_api.py`.

**Interfaces:**
- Produces in `app/scoring.py`: `WRONG_DECLINE_COST = 1500`, `NEEDLESS_REFER_COST = 50`, `PAYOUT_TOLERANCE = 1.0`. `score_claim(agent: dict | None, handler: dict) -> dict` with `decision_ok`, `accurate`, `mistake_cost`; no decision counts as `refer`. `measures(rows: list[dict], total_seconds: float) -> dict` with `claims`, `decision_accuracy`, `accuracy`, `mistake_cost`, `straight_through`, `cost_per_claim`, `seconds_per_claim`, `total_seconds`. `evaluation_report(engine, run_id) -> dict`: the run (set, tiny, N, model, effort, status), `measures`, and one row per claim (copy id, original id, agent's decision, payout and reason, handler's decision and payout, `decision_ok`, `accurate`, `mistake_cost`, cost, seconds).
- The cost of a mistake: the agent declines what the handler did not, $1,500; approves what the handler did not, its payout, paid or not; refers what the handler did not, $50; approves with the handler but more than a dollar off, the difference. Straight-through: the share of claims the agent did not refer.
- API: `GET /evaluations` (`list_evaluations`), newest first, each with its run and `measures`; `GET /evaluations/{run_id}` (`get_evaluation`), 404 for a dispatch or an unknown run.
- Frontend: a **Scoreboard** link in the nav; `/scoreboard` lists the evaluations with every measure and a form to start one (set, whole or tiny, N from 1 to 25 with 3 to start with); `/scoreboard/:id` lists its claims, the handler's decision and payout beside the agent's, misses marked `.miss`, each claim linked to its page. A stopped evaluation shows as stopped.

- [ ] **Step 1: Write the failing tests**

```python
HANDLERS = [
    {"claim_id": "A", "decision": "approve", "payout": 500.0},
    {"claim_id": "B", "decision": "decline", "payout": 0.0},
    {"claim_id": "C", "decision": "refer", "payout": 0.0},
    {"claim_id": "D", "decision": "approve", "payout": 1200.0},
    {"claim_id": "E", "decision": "approve", "payout": 300.0},
]
AGENT = {
    "A": {"decision": "approve", "payout": 500.60},   # right, within a dollar
    "B": {"decision": "approve", "payout": 800.0},    # wrongful approval: $800
    "C": {"decision": "decline", "payout": 0.0},      # wrongful decline: $1,500
    "D": {"decision": "approve", "payout": 1000.0},   # right decision, $200 off
    "E": None,                                        # no decision: a needless referral, $50
}

def test_measures_match_ones_worked_out_by_hand():
    rows = [{**score_claim(AGENT[h["claim_id"]], h), "cost_usd": 0.01, "seconds": 10.0} for h in HANDLERS]
    m = measures(rows, total_seconds=25.0)
    assert m["decision_accuracy"] == 2 / 5             # A and D
    assert m["accuracy"] == 1 / 5                      # A only
    assert m["mistake_cost"] == 800 + 1500 + 200 + 50
    assert m["straight_through"] == 4 / 5              # E counts as referred
    assert m["cost_per_claim"] == pytest.approx(0.01) and m["seconds_per_claim"] == 10.0 and m["total_seconds"] == 25.0

def test_evaluations_are_listed_newest_first_with_every_measure(client, two_evaluations): ...
def test_an_evaluation_opens_to_its_claims_beside_the_handlers(client, evaluated): ...
def test_a_dispatch_is_not_an_evaluation(client, dispatched): ...   # 404
```

  Frontend: `Scoreboard` "lists the evaluations newest first with every measure"; "starts an evaluation with the set, tiny and N chosen"; `Evaluation` "marks the misses and links each claim". E2e, after `01` and `02`:

```ts
test('an evaluation started on the dashboard reaches the service with N and shows on the scoreboard', async ({ page, request }) => {
  await page.goto('/scoreboard')
  await page.getByLabel('Set').selectOption('held_out')
  await page.getByLabel('Only the five tiny claims').check()
  await page.getByLabel('Claim agents at once').fill('5')
  await page.getByRole('button', { name: 'Start evaluation' }).click()
  await expect.poll(async () => (await (await request.get('http://127.0.0.1:8003/received')).json()).starts)
    .toContainEqual({ path: '/evaluations', body: { eval_set: 'held_out', tiny: true, workers: 5 } })
  const row = page.locator('tbody tr').first()
  await expect(row).toContainText('Finished', { timeout: 60_000 })
  for (const measure of ['Decision accuracy', 'Accuracy', 'Cost of mistakes', 'Straight-through', 'Cost per claim', 'Time per claim', 'Total time'])
    await expect(page.getByRole('columnheader', { name: measure })).toBeVisible()
  await row.getByRole('link').click()
  await expect(page.locator('tbody tr')).toHaveCount(5)
})

test('an evaluation played out through the API appears on the scoreboard', async ({ page, request }) => {
  expect((await request.post('/api/evaluations', { data: { eval_set: 'evaluation', tiny: true, workers: 3 } })).status()).toBe(202)
  await page.goto('/scoreboard')
  await expect(page.locator('tbody tr', { hasText: 'evaluation' }).first()).toContainText('Finished', { timeout: 60_000 })
})
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Copy the lab's names for the measures where the brief keeps them (`decision_accuracy`, `accuracy`).
- [ ] **Step 4: Run `npm run check`** with `.env` moved aside; put it back. Expected: green.
- [ ] **Step 5: Commit** `feat(scoreboard): measures for every evaluation, and each claim beside the handler's`.

### Task 10: The documents

**Files:** Modify `docs/SPEC.md` (sections 5, 6 and 7), `docs/TECH_STACK.md`, `docs/PROCESS.md`, `docs/CODE_STANDARDS.md`, `docs/STATUS.md`, `COMMANDS.md`, `README.md`.

- `SPEC.md`: working the queue by dispatch, N, the summary; the Scoreboard; cost and time. `git diff` over sections 3.3 and 4 must be empty.
- `TECH_STACK.md`: the dispatcher, `RetryPolicy` and `error_handler`, a saver per claim run, `MODEL_NAME`, the default model, the new routes.
- `PROCESS.md`: live runs are the dispatches and evaluations of "What done looks like", in place of the five-claim cap.
- `CODE_STANDARDS.md`: the dispatcher as the fifth part; claim agents share a process's 4 connections and wait for one.
- `STATUS.md`: what works now; the live results come in Task 11.
- `COMMANDS.md`, `README.md`: `npm run seed`, `uv run -m agent CLM-1001`, the Scoreboard.

- [ ] **Step 1: Write them**, then run each command in `COMMANDS.md` that spends nothing.
- [ ] **Step 2: Commit** `docs: describe the dispatcher, evaluations and the scoreboard`.

### Task 11: Live verification

Needs the owner. This spends money on OpenRouter and writes to Supabase and Langfuse. The runs below work about 80 claims on DeepSeek V4.1 Flash; the lab paid about $0.002 a claim, so about $0.20 in all. Do Step 1 before starting.

**Files:** Extend `scripts/check_live.py` (`--run N`: the run's claims with start and finish times, the most at once, the sum of their times against the run's; `--tag run-N`: the Langfuse traces with that tag). Record results in `docs/STATUS.md`.

- [ ] **Step 1: Prepare with the owner.** Close the lab notebooks; stop any old servers; open the session at the repo root and sign in to the Supabase MCP server with `/mcp`. Take a baseline of `pg_stat_activity` for user postgres.
- [ ] **Step 2: Reseed once.** `npm run seed`. Expected: 25 claims waiting.
- [ ] **Step 3: N=3, killed partway.** `npm run dev`; through the Playwright MCP server start a dispatch at N=3. Once a few claims are decided and others are in progress, kill every process listening on 8002. Expected: `npm run dev` stops the others. Start `npm run dev` again. Expected: the run shows as stopped.
- [ ] **Step 4: N=25.** Start a dispatch at N=25. Expected: the claims left in progress keep their threads and attempts; the rest start within a few seconds of each other; the run takes well under the sum of its claims' times; claims worked from the start take about as long as at N=3, or the report says where the time went. During it, through the MCP server, no more than 12 connections for user postgres, and no claim fails for want of a connection.
- [ ] **Step 5: Sign-off.** Sign off a held claim on the dashboard. Expected: paid on screen within five seconds.
- [ ] **Step 6: Evaluations.** Note the queue's counts. Start an evaluation of the evaluation set at N=25, then one of the held-out set's `tiny` claims at N=5. Expected: both on the scoreboard with every measure; the queue's counts unchanged.
- [ ] **Step 7: Langfuse.** `python scripts/check_live.py --tag run-<N=25 dispatch>`. Expected: every claim of that dispatch.
- [ ] **Step 8: Commit** `docs: record the live verification of the swarm`.

## Done means

Each line under "What done looks like" in `docs/SWARM.md` has its evidence: tests in Tasks 1 to 9, documents in Task 10, measurements in Task 11.
