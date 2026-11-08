# An improver for the claim agent, and handlers who sign in: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Tick them off here.

**Goal:** Handlers sign in. The claim agent's configuration and the improver's recipe are kept in versions. A judge grades every explanation, and an improver, run from the dashboard, proposes changes that a handler approves and code keeps or rolls back on the held-out set, within a spending limit. Everything sits on a dashboard that looks like enterprise software.

**Architecture:** The backend (`backend/app/`) owns every record: handlers by email, settings, configuration and recipe versions, rounds and proposals, the judge (`app/judge.py`) and the keep-or-roll-back rule (`app/improvement.py`). The agent's service builds each run's claim agents from the run's version, and hosts the improver (`agent/improver.py`), a LangChain `create_agent` with `HumanInTheLoopMiddleware` and a checkpointer of its own. The improver reaches the records only through `agent/improver_backend.py`. The dashboard signs in with Supabase Auth and sends the access token with every action; the backend checks it against the project's public keys.

**Tech Stack:** FastAPI, SQLAlchemy Core, Supabase Postgres and Auth, PyJWT, the `openrouter` package (Jev's Decisions endpoint), LangGraph and LangChain (`create_agent`, `HumanInTheLoopMiddleware`, `ModelCallLimitMiddleware`), Langfuse, React with `@supabase/supabase-js`, Vitest, Playwright.

**Spec:** `docs/IMPROVE.md`. Read it alongside this plan. Where they disagree, the spec wins unless a line under "Decisions" says otherwise.

**Sidenote from the brief:** For consistency with the course, please read `labs/week2day5.ipynb` and build the judge, the configuration's versions and the improver in the same style as the lab. Where the lab and this brief differ, follow the brief. In this repo the lab is `labs/week2day5.ipynb` (the course's own copy); `labs/week2day5_mex.ipynb` is our earlier exercise.

## Decisions

The brief was written against the course's own build. The owner chose the model in an earlier change, and left every other decision to the plan.

| The brief says | This repo | Decision |
|---|---|---|
| Version 1 on `openai/gpt-6-luna` at low effort; the improver on Luna at medium; "under $1 on GPT-6 Luna" | The owner kept `deepseek/deepseek-v4.1-flash` at low effort in place of Luna | Version 1 is DeepSeek V4.1 Flash at `low`. The improver is DeepSeek V4.1 Flash at `low`, the one level the lab allows that model. The live checks aim for under $1 on it |
| `docs/BRAND.md` | `docs/DESIGN.md` is our visual style | Every look-and-feel requirement is checked against `docs/DESIGN.md`. No new file |
| `agent/api.py`, the claim agent's client, and `test_the_agent_cant_read_the_scoreboard` | The agent calls `app` operations in Python (owner's decision in `PLAN_DURABLE.md`) | "A client module" means a Python module that is the only way in. The claim agent's modules (`graph.py`, `tools.py`, `worker.py`, `dispatcher.py`, `prompts.py`) never import `app.scoring`, `app.improvement` or `app.judge`, nor name `answer_key`. Only `agent/improver_backend.py` reaches them for the improver. A test walks the imports |
| `handler_decisions` with decision, payout, `tiny` | `answer_key` (decision, payout, reason) and `eval_sets` (`tiny`) | `answer_key` gains `reference` |
| A `handlers` table, with no email column | Handlers are read from `data/handlers.json`, which already has each email | No table. The backend finds the handler by email in `data/handlers.json`. Marcus and Priya get `can_approve_over_limit: false` |
| `SignOffForm.tsx`; `sign_off_claim` takes a `handler_id` | `frontend/src/pages/Approvals.tsx` with an "Approve as" list; `POST /payments/{id}/approve` with `{handler_id}` | The list goes. The route takes no body and records the signed-in handler. Its `operation_id` stays `sign_off_payment` |
| `dashboard/`, `styles.css`, `dashboard/vite.config.ts` | `frontend/`, `frontend/src/styles/tokens.css` and `app.css`, `frontend/vite.config.ts` | Same intent, our paths |
| Service writes under `/api/runs`; the agent's calls wait 30 seconds | The service writes through Python, with no HTTP | Nothing to keep open. Grading still finishes in under 20 seconds, so finishing a run stays quick |
| A run with no reasoning effort records `none` | We record `none` | Record `default`, as the brief asks |
| `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY`, `HANDLER_PASSWORD` in `.env` | `.env` has `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` and `HANDLER_PASSWORD` (checked by name only) | **The owner adds the two `VITE_` lines** with the same values (Task 1, Step 1). Nothing reads `SUPABASE_URL` |
| `docs/CODE_STANDARDS.md` rules 7, 13 and 14 | Our standards are not numbered | Rule 7 → "The five parts of the agent", now six. Rule 13 → a new "Clients" section: the improver's module and the dashboard's Supabase client. Rule 14 → "The four parts" item 1, prompts: instructions are written as goals |
| The lab's models allowed, prices as strings | | `data/models.json` with the lab's three models, efforts and prices as numbers: Luna 0.10/0.50, Gemini 3.5 Flash Lite 0.30/2.50, DeepSeek V4.1 Flash 0.30/1.20 (dollars per million tokens in and out). The owner chose the default, not the list, so Luna stays allowed to the improver |

These follow from the brief, the lab and the code:

1. Starting values, from the lab: objectives accuracy 0.6 and grade 1.0; noise accuracy 0.1, grade 0.15, cost per claim 0.15, time per claim 0.15; tolerance accuracy 0.1, grade 0.15, cost per claim 1.0, time per claim 0.25; spending limit per round $0.50; agents at once 25; approval limit $1,000. They go in `data/improvement.json`, apart from the approval limit, which stays in `data/company.json`.
2. A decision records the approval limit in force when it is recorded (`decisions.approval_limit`). `needs_sign_off` and `pay` use the decision's own limit, so a new limit applies to decisions recorded after it, and never to one already held.
3. The judge's grades are stored per claim of an evaluation in a new table `judge_grades(run_id, claim_id, score, letter, same_grounds, same_section, cost_usd, error)`. `score` null means not graded.
4. The improver's model calls are capped at 30 per round with LangChain's `ModelCallLimitMiddleware(run_limit=30, exit_behavior="end")`, beside the claim agent's 30.
5. A round is in one of these stages, recorded in `rounds.stage`: `first_evaluations`, `improving`, `awaiting_change`, `evaluating`, `verdict`, `awaiting_recipe`, `finished`, `stopped`; with `rounds.progress` holding "2 of 3" during the two evaluation stages.
6. Settings are one row per name in `settings(name, value JSON, changed_by, changed_at)`, with every change also in `settings_history`. A round copies the improvement settings into `rounds.settings` when it starts and works only from that copy.
7. The improver's checkpointer is `make_checkpoints` from `agent/checkpoints.py` (Postgres on Supabase, SQLite in tests), its saver used only by the improver. Each round's thread id is `round-<round_id>-<8 hex>`.
8. In the end-to-end tests, the improver runs on `ScriptedImproverModel` (`AGENT_PROVIDER=scripted`): it reads the results, proposes one added line, runs the evaluation, then proposes a recipe with one added line.

## Global Constraints

- "Nothing it proposes takes effect until a handler approves it."
- "Only a handler who can sign off over the limit may start or stop a round, approve or reject the improver's proposals or change a setting." Everyone else sees why, and the backend refuses with 403.
- "Only one dispatch, evaluation or stretch of the improver's work runs at a time." A refusal names what is running. While a round waits for a handler, dispatches and evaluations can run.
- "From the held-out set it sees the scores only." The claim agent never sees the handlers' decisions.
- The judge: `typesafe/jev-1.13`; each call times out after 10 seconds, with retries off; finishing stays under 20 seconds; the letter is the most likely level, never the score rounded.
- A kept version: meets the objectives, at least one measure improves by more than its noise, none gets worse by more than its tolerance. Accuracy and grade compare as differences; cost and time as fractions of the old value. No grade means rolled back.
- Every API operation has an `operation_id`, a one-line docstring and no optional inputs.
- Tests never call a model, the judge or Supabase Auth. `npm run check` never reads `.env`.
- Everything `npm run dev` starts works on Windows. Everything follows `docs/DESIGN.md` in light and dark mode on a laptop screen.
- Live runs: one `npm run seed`, then only the runs in "What done looks like".

## Review Focus

1. A handler's token expires during a long session: the next action gets a 401, and the dashboard signs them out with a message rather than failing silently. Tests in Tasks 1 and 8.
2. The service restarts while a round waits for approval: the round is still waiting after the restart, and approving it resumes the improver from its checkpoint. Test in Task 6.
3. Two handlers approve the same proposal at once: one is recorded, the other is told it was already decided. Test in Task 5.
4. A round's spending limit runs out partway through its third evaluation: no new claim starts, the evaluation shows stopped, and the version is rolled back. Test in Task 6.
5. Jev times out on some claims of an evaluation: those show as not graded, the average leaves them out, and finishing still takes under 20 seconds. Test in Task 4.

---

## File structure

```
backend/app/    auth.py (token -> handler), settings_store.py (settings and their history), configs.py (configuration and
                recipe versions, diffs), judge.py (Jev, the one call), improvement.py (version scores, the rule, the
                improver's view, the spending estimate, rounds and proposals), plus db.py, seed.py, api.py, claims.py,
                payments.py, scoring.py, runs.py, evaluations.py
backend/agent/  improver.py (the sixth part), improver_backend.py (its only way to the records), improver_instructions.md,
                recipe_v1.md, plus service.py, worker.py, dispatcher.py, prompts.py, tools.py, scripted.py, tracing.py
data/           improvement.json (settings' starting values), models.json (models allowed)
frontend/src/   auth/ (supabase client, AuthProvider, SignIn), components/ (PageHeader, StatusPill, DiffView,
                ConfirmDialog, States, ScoreChart), pages/ (Configuration, Settings, Improvement; Instructions goes)
e2e/            stand_in_auth.py, stand_in_judge.py, tests/04-improvement.spec.ts, 05-settings.spec.ts
```

---

### Task 1: Handlers sign in

**Files:** Create `backend/app/auth.py`, `frontend/src/auth/supabase.ts`, `frontend/src/auth/AuthProvider.tsx`, `frontend/src/pages/SignIn.tsx`, `e2e/stand_in_auth.py`. Modify `data/handlers.json`, `backend/pyproject.toml` (`uv add "pyjwt[crypto]"`), `backend/app/config.py`, `backend/app/api.py`, `backend/app/payments.py`, `frontend/package.json` (`@supabase/supabase-js`), `frontend/src/api.ts`, `frontend/src/App.tsx`, `frontend/src/pages/Approvals.tsx`, `frontend/vite.config.ts`, `scripts/dev.mjs`, `e2e/playwright.config.ts`, every e2e spec. Test `backend/tests/test_auth.py`, `test_api.py`, `frontend/src/auth/*.test.tsx`, `pages/Approvals.test.tsx`.

**Interfaces:**
- `Settings.supabase_url: str | None`, from `VITE_SUPABASE_URL` in the process environment only, like `DATABASE_URL`.
- `app/auth.py`: `Handler` (dataclass: `handler_id`, `name`, `role`, `email`, `can_approve_over_limit`); `handler_from_token(token: str, settings: Settings, ref: Reference, jwks: PyJWKClient | None = None) -> Handler`, which checks the signature against `<supabase_url>/auth/v1/.well-known/jwks.json` with `PyJWKClient` (cached), the audience `authenticated` and the expiry, then finds the handler by the token's `email`; raises `NotSignedIn` (401) or `UnknownHandler` (403). FastAPI dependencies `signed_in(...) -> Handler` and `senior(...) -> Handler`, the second raising 403 "only a handler who can sign off payouts over the limit can do this" for the others.
- Backend: `POST /dispatches`, `POST /evaluations` and `POST /payments/{id}/approve` need `signed_in`. The approval takes no body. `GET /me` (`get_signed_in_handler`) returns the handler. Reads stay open, as the brief says only actions are checked.
- Dashboard: `supabase.ts` makes one client from `import.meta.env.VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY`. `AuthProvider` keeps the session, shows `SignIn` (email and password, `signInWithPassword`, no sign-up link) until there is one, and offers `useAuth() -> {handler, token, signOut}`. `api.ts`'s `request` sends `Authorization: Bearer <token>` on every POST and PUT, and a 401 signs out with "Your session ended. Please sign in again." The header shows the handler's name and role and a Sign out button.
- `frontend/vite.config.ts`: `envDir` is the repo root for `npm run dev`; Vitest sets its own values in `test.env`.
- `e2e/stand_in_auth.py` on port 8004: an ES256 key of its own; `POST /auth/v1/token?grant_type=password` for the three handlers with the password `e2e-password`, returning a Supabase session (`access_token`, `refresh_token`, `expires_in`, `token_type`, `user`); `GET /auth/v1/.well-known/jwks.json`; `GET /auth/v1/user`; CORS for the dashboard's origin. The e2e backend and dashboard get `VITE_SUPABASE_URL=http://127.0.0.1:8004`. Every spec signs in as Dana Whitfield in a `beforeEach`.

- [ ] **Step 1: With the owner.** Add `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` to `.env`, with the values of `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`. Check, as the lab's last cells do, that sign-ups are off, that the three handlers can sign in with `HANDLER_PASSWORD` and that the project publishes its public keys (it does: ES256, checked on 2026-11-06). Expected: "Sign-ups are off", three sign-ins.
- [ ] **Step 2: Write the failing tests**

```python
def test_a_valid_token_finds_the_handler_by_email(keys): ...           # a key pair made in the test, a stub PyJWKClient
def test_no_token_an_expired_one_or_another_audience_is_refused(keys): ...   # 401 each
def test_a_token_for_an_unknown_email_is_403(keys): ...
@pytest.mark.parametrize("method,path", [("POST", "/dispatches"), ("POST", "/evaluations"), ("POST", "/payments/PAY-CLM-1003/approve")])
def test_every_handler_action_needs_a_sign_in(client, method, path): ...       # 401 without a token
def test_a_sign_off_records_the_signed_in_handler(client, signed_in_as): ...   # approved_by == "HND-01"
def test_marcus_cannot_sign_off_over_the_limit(client, signed_in_as): ...      # 403, the payment still held
```

  Front end: `SignIn` "signs in with email and password and offers no sign-up"; `App` "shows the sign-in until there is a session, then the handler's name and Sign out"; `Approvals` "has no list of names, and tells Marcus he can't sign off over the limit, and why"; `api` "sends the token on an action and signs out on a 401" (Review Focus 1).
- [ ] **Step 3: Run them.** Expected: FAIL.
- [ ] **Step 4: Implement.** Port every e2e spec to sign in first, through the stand-in.
- [ ] **Step 5: Run `npm run check`** in a clean worktree with no `.env`. Expected: green.
- [ ] **Step 6: Commit** `feat(auth): handlers sign in with Supabase Auth; the backend checks the handler on every action`.

### Task 2: Settings, and an approval limit that is a setting

**Files:** Create `backend/app/settings_store.py`, `data/improvement.json`, `data/models.json`, `frontend/src/pages/Settings.tsx`. Modify `backend/app/db.py`, `seed.py`, `claims.py`, `payments.py`, `api.py`, `backend/agent/tools.py`, `backend/app/reference.py` (allow the two new files), `frontend/src/api.ts`, `App.tsx`, `styles/tokens.css`. Test `backend/tests/test_settings.py`, `frontend/src/pages/Settings.test.tsx`, `e2e/tests/05-settings.spec.ts`.

**Interfaces:**
- Tables `settings(name, value, changed_by, changed_at)` and `settings_history(id, name, value, changed_by, changed_at)`; column `decisions.approval_limit`. Seeding fills `settings` from `data/company.json` (`approval_limit`) and `data/improvement.json` (`objectives`, `noise`, `tolerance`, `round_spending_limit`, `agents_at_once`), with `changed_by` "seed".
- `settings_store.get_settings(engine) -> dict`; `change_setting(engine, name: str, value, handler: Handler) -> dict` (validates: limit and spending > 0, floors and noise in their ranges, agents at once 1 to 25); `models_allowed(ref, settings: Settings) -> dict` from `data/models.json`, or only `MODEL_NAME` at effort `default` and price 0 when it is set.
- Every reader of `payout_approval_limit` reads `get_settings(engine)["approval_limit"]`; `record_decision` stores it on the decision; `needs_sign_off` and `pay` use the decision's own.
- API: `GET /settings` (`get_settings`), with the models allowed and who changed what; `PUT /settings/{name}` (`change_setting`, body `{value}`) needs `senior`.
- Settings page: every setting with who changed it and when; each field editable for a senior handler, behind a confirmation in the dashboard's own dialog (Task 8's `ConfirmDialog`, a plain one until then); for the others, read-only with "Only a handler who can sign off payouts over the limit can change settings." The models allowed as a read-only table, prices right-aligned in "$ per million tokens". Appearance: Light, Dark, Same as this computer, kept in `localStorage` and set as `data-theme` on `<html>`; `tokens.css` reads colors from `[data-theme]` as well as `prefers-color-scheme`.

- [ ] **Step 1: Write the failing tests**

```python
def test_settings_start_with_the_labs_values(engine):
    s = get_settings(engine)
    assert s["approval_limit"] == 1000 and s["objectives"] == {"accuracy": 0.6, "grade": 1.0}
    assert s["noise"] == {"accuracy": 0.1, "grade": 0.15, "cost_per_claim": 0.15, "seconds_per_claim": 0.15}
    assert s["tolerance"] == {"accuracy": 0.1, "grade": 0.15, "cost_per_claim": 1.0, "seconds_per_claim": 0.25}
    assert (s["round_spending_limit"], s["agents_at_once"]) == (0.5, 25)

def test_a_change_records_who_and_when(engine): ...
def test_a_new_approval_limit_applies_to_the_next_decision(env):
    record(env, "CLM-1003", approve(3750.0))                      # held under $1,000
    change_setting(env.engine, "approval_limit", 5000, DANA)
    record(env, "CLM-1008", approve(2240.0))
    assert needs_sign_off(env.engine, env.ref, "CLM-1003") and not needs_sign_off(env.engine, env.ref, "CLM-1008")
def test_marcus_cannot_change_a_setting(client, signed_in_as): ...   # 403, nothing recorded
def test_models_allowed_with_model_name_is_that_model_at_default_and_free(monkeypatch): ...
```

  Front end: "shows each setting with who changed it", "asks for confirmation before a change", "is read-only for Marcus, saying why", "remembers the appearance in this browser". E2e `05-settings.spec.ts`: Dana changes the spending limit, the page shows her as the one who changed it; the theme switch sets `data-theme="dark"` and survives a reload.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `npm run check`.** Expected: green.
- [ ] **Step 5: Commit** `feat(settings): the platform's settings on their own page; the approval limit is one of them`.

### Task 3: The configuration in versions

**Files:** Create `backend/app/configs.py`, `backend/agent/recipe_v1.md`, `frontend/src/pages/Configuration.tsx`, `frontend/src/components/DiffView.tsx`. Delete `frontend/src/pages/Instructions.tsx`. Modify `backend/app/db.py`, `seed.py`, `runs.py`, `evaluations.py`, `api.py`, `backend/agent/prompts.py`, `worker.py`, `models.py`, `service.py`, `frontend/src/App.tsx`. Test `backend/tests/test_configs.py`, `test_dispatcher.py`, `test_evaluations.py`, `frontend/src/pages/Configuration.test.tsx`, `components/DiffView.test.tsx`.

**Interfaces:**
- Tables `config_versions(version, instructions, model, reasoning_effort, summary, reason, proposed_by, approved_by, approved_at, round_id, created_at)`, `recipe_versions(version, recipe, summary, reason, proposed_by, approved_by, approved_at, round_id, created_at)` and `current_version(kind, version)`. Seeding makes version 1 of each: the configuration from `agent/instructions_claim.md` with `deepseek/deepseek-v4.1-flash` at `low` (or `MODEL_NAME` at `default`), `proposed_by` "seed"; the recipe from `agent/recipe_v1.md`, the lab's first recipe verbatim.
- `configs.current(engine, kind: Literal["config", "recipe"]) -> dict`; `get_version(engine, kind, version) -> dict`; `add_version(engine, kind, fields: dict, approved_by: str, round_id: int | None) -> int`; `make_current(engine, kind, version) -> None`; `diff(old: str, new: str) -> list[dict]` with `{"kind": "same" | "add" | "remove", "text"}` per line, from `difflib.ndiff`; `config_for_run(engine, run_id) -> dict` returns only `instructions`, `model`, `reasoning_effort`, `version`: no scores.
- `runs.config_version` and `runs.round_id`. `start_dispatch` and `start_evaluation` record the current version; a round's evaluations pass the version under test.
- `prompts.claim_prompt(ref, instructions: str)`: the version's instructions, then the section index. `worker`: `AgentEnv` builds the model and graph for a run's version (`env.claim_graph(version)`), caching one chat model per (model, effort). The run's `model` and `reasoning_effort` come from the version; no effort is recorded as `default`.
- API: `GET /configuration` (`get_configuration`): the current version, every version with its held-out scores (Task 5's `version_scores`, empty until then), and the recipes; `GET /configuration/{kind}/{version}/diff` (`get_version_diff`) against the version before. The Instructions route goes; `GET /instructions` is replaced.
- `DiffView`: added lines on the success tint with "+", removed on the danger tint with "−", unchanged lines folded to "… 12 unchanged lines" when more than 6, and a line on top with the model and effort before and after. Used for versions and recipes alike.

- [ ] **Step 1: Write the failing tests**

```python
def test_version_1_is_the_claim_agent_as_it_is(engine): ...          # instructions file, deepseek at low, proposed_by seed
def test_a_dispatch_and_an_evaluation_record_their_version(env): ...
def test_a_run_uses_its_versions_instructions_and_model(env): ...      # a version 2 made current: the stub sees its text
def test_the_claim_agents_view_of_its_version_holds_no_scores(engine): ...
def test_diff_marks_added_and_removed_lines(): ...
def test_no_effort_is_recorded_as_default(monkeypatch, env): ...
```

  Front end: `Configuration` "shows the current version and every version with its held-out scores"; "opens any version's diff from the one before"; `DiffView` "colors additions and removals and folds long unchanged runs".
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `npm run check`.** Expected: green.
- [ ] **Step 5: Commit** `feat(configs): the claim agent's configuration and the recipe in versions; runs record theirs`.

### Task 4: The judge

**Files:** Create `backend/app/judge.py`, `e2e/stand_in_judge.py`. Modify `backend/pyproject.toml` (`uv add openrouter`), `backend/app/config.py`, `db.py`, `seed.py`, `runs.py` (`finish_run`), `scoring.py`, `api.py`, `frontend/src/pages/Scoreboard.tsx`, `Evaluation.tsx`, `e2e/playwright.config.ts`. Test `backend/tests/test_judge.py`, `test_scoring.py`, frontend page tests.

**Interfaces:**
- `answer_key.reference`, seeded from `data/evals/`. Table `judge_grades` (Decision 3).
- `Settings.judge_url: str`, from `OPENROUTER_DECISIONS_URL` in the process environment, default `https://openrouter.ai`.
- `app/judge.py`: `JEV = "typesafe/jev-1.13"`; `RUBRIC` and `JUDGE_QUESTIONS` copied from the lab; `judge(handler: dict, agent: dict, settings: Settings, client=None) -> dict` with `score`, `letter`, `same_grounds`, `same_section` (probabilities) and `cost_usd`; the call, written once, is `client.alpha.decisions.create(model=JEV, state=..., questions=JUDGE_QUESTIONS, server_url=settings.judge_url, timeout_ms=10_000, retries=<off>)`. Before writing it, confirm the parameter names with `inspect.signature` on the installed package and write them down here. The state is `{"handler": {decision, reason, reference}, "agent": {decision, reason, section}}`, where the agent's section is its decision's first citation. The letter is `GRADES[argmax(probabilities)]`, never `round(score)`.
- `grade_evaluation(engine, run_id, settings) -> None`: a thread pool over the run's claims; a claim with no decision gets F with score 0 and no call; a call that fails or times out stores `score` null with its error. With `MODEL_NAME` set the judge is not called and every claim shows as not graded. `finish_run` calls it for an evaluation, before it marks the run finished.
- `scoring.measures` gains `grade` (the mean of graded scores, or null) and `judge_cost_usd`; `evaluation_report` adds each claim's `score`, `letter`, `same_grounds`, `same_section`. The claim agent's cost stays apart.
- Scoreboard: a "Grade" column, "1.85 (B)", right-aligned. Evaluation page: each claim's letter in a colored pill, its score, and "Same grounds: yes/no" and "Same section: yes/no" from probability ≥ 0.5; "Not graded" when null.
- `e2e/stand_in_judge.py` on port 8005: `POST /api/alpha/decisions` answering B with probability 0.7 for any claim, `usage.cost` 0.0001. The e2e backend and service get `OPENROUTER_DECISIONS_URL=http://127.0.0.1:8005`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_letter_is_the_most_likely_level_not_the_score_rounded():
    answers = stub_answers(probabilities={"0": 0.05, "1": 0.45, "2": 0.10, "3": 0.40}, score=1.85)
    assert judge(HANDLER, AGENT, SETTINGS, client=StubJudge(answers)) | {"letter": "C", "score": 1.85}
def test_an_undecided_claim_is_graded_f_without_asking(env): ...
def test_an_ungraded_claim_is_left_out_of_the_average(): ...
def test_a_judge_that_times_out_leaves_the_claim_ungraded_and_finishing_quick(env): ...   # Review Focus 5: < 20 s with 25 slow claims
def test_the_judges_cost_is_recorded_apart_from_the_agents(env): ...
def test_with_model_name_the_judge_is_not_called(env, monkeypatch): ...
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `npm run check`.** Expected: green.
- [ ] **Step 5: Commit** `feat(judge): Jev grades every explanation in an evaluation against the handler's reason`.

### Task 5: Rounds, proposals and the rule, in the backend

**Files:** Create `backend/app/improvement.py`. Modify `backend/app/db.py`, `seed.py`. Test `backend/tests/test_improvement.py`.

**Interfaces:**
- Tables `rounds(round_id, stage, progress, recipe_version, settings, thread_id, version_tried, reason, before, after, verdict, why, started_by, stopped_by, started_at, finished_at)` and `proposals(id, round_id, kind, payload, status, refusal, reason, decided_by, decided_at, created_at)` with `kind` "change" or "recipe" and `status` "waiting", "approved", "rejected".
- `version_scores(engine, version: int) -> dict[str, dict]`: per set, the averages of the finished whole-set evaluations that rounds ran on the version (`runs.round_id` set, `tiny` false), with `runs` (how many) beside them.
- `needs_first_evaluations(engine, version) -> list[str]`: the sets still owed, one evaluation and two held-out.
- `compare(new, old, noise, tolerance) -> (better, worse)` and `keep_or_roll_back(new: dict, old: dict, settings: dict) -> tuple[bool, str]`, from the lab, with "no grade" rolled back first.
- `improver_view(engine, round_id) -> dict`: the lab's `read_results` report: the current version; its scores on both sets; from its latest evaluation-set run, the wrong claims and the five right ones with the lowest grades, each with the handler's decision, payout, reason and reference beside the agent's, and the judge's score, letter and two checks; the earlier rounds; the objectives, noise, tolerance and the rule's words; the spending left; the models allowed. Nothing from the held-out set but scores.
- `estimate(engine, model: str, claims: int, models: dict) -> float`: the brief's rule (highest cost per claim seen for the model, or the current model's scaled by the larger price ratio).
- `round_spent(engine, round_id) -> float`: the improver's model calls, every evaluation's claim agents and the judge.
- `start_round` (raises `RoundOpen` while another round is open), `set_stage`, `add_proposal`, `decide_proposal(engine, proposal_id, approve: bool, reason: str, handler) -> dict`, `record_verdict`, `stop_round(engine, round_id, handler) -> None`. A stopped round refuses every later write with `RoundStopped`. `decide_proposal` on a decided proposal raises `AlreadyDecided`.

- [ ] **Step 1: Write the failing tests**

```python
OLD = {"accuracy": 0.70, "grade": 1.50, "cost_per_claim": 0.0010, "seconds_per_claim": 20.0}

def test_a_version_is_kept_when_one_measure_improves_beyond_its_noise():
    new = {**OLD, "accuracy": 0.82}                       # +0.12 > 0.1
    assert keep_or_roll_back(new, OLD, LAB_SETTINGS) == (True, "accuracy improved")

def test_cost_and_time_compare_as_fractions_of_the_old_value():
    assert keep_or_roll_back({**OLD, "cost_per_claim": 0.0008}, OLD, LAB_SETTINGS)[0]       # 20% cheaper > 15%
    assert not keep_or_roll_back({**OLD, "accuracy": 0.82, "seconds_per_claim": 25.1}, OLD, LAB_SETTINGS)[0]  # > 25% slower

def test_a_version_below_the_objectives_is_rolled_back(): ...       # accuracy 0.55 < 0.6
def test_a_version_with_no_grade_is_rolled_back(): ...
def test_a_versions_scores_count_only_its_rounds_whole_set_evaluations(engine): ...   # a Scoreboard and a tiny run left out
def test_the_improvers_view_holds_nothing_from_the_held_out_set_but_scores(engine): ...
def test_two_approvals_of_one_proposal_record_one(engine): ...      # Review Focus 3
def test_a_stopped_round_refuses_every_later_write(engine): ...
def test_a_round_keeps_its_settings_when_they_change(engine): ...
def test_only_one_round_is_open_at_a_time(engine): ...           # RoundOpen
def test_the_estimate_for_a_model_with_no_runs_scales_by_its_prices(engine): ...
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run all backend tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(improvement): rounds, proposals, version scores and the rule that keeps or rolls back`.

### Task 6: The improver

**Files:** Create `backend/agent/improver.py`, `backend/agent/improver_backend.py`, `backend/agent/improver_instructions.md`. Modify `backend/agent/service.py`, `dispatcher.py`, `scripted.py` (`ScriptedImproverModel`), `tracing.py`, `backend/app/api.py`, `agent_client.py`, `backend/tests/test_evals_isolation.py`. Test `backend/tests/test_improver.py`, `test_service.py`.

**Interfaces:**
- `improver_backend.py`: the only module of the agent that imports `app.improvement`, `app.scoring` or `app.configs`' version writes. The isolation test checks that `graph.py`, `tools.py`, `worker.py`, `dispatcher.py` and `prompts.py` import none of them and never name `answer_key`.
- `improver.py`: `make_improver(env, round_id, recipe: str, checkpointer)` with `create_agent(model, tools=[read_results, propose_change, run_evaluation, propose_recipe], system_prompt=..., middleware=[HumanInTheLoopMiddleware(interrupt_on={"propose_change": {"allowed_decisions": ["approve", "reject"]}, "propose_recipe": ...}), ModelCallLimitMiddleware(run_limit=30, exit_behavior="end")], checkpointer=checkpointer)`; the model is DeepSeek V4.1 Flash at `low` with `parallel_tool_calls=False` (or `MODEL_NAME` at `default`), each call recorded with the round (`usage.record_model_call` with `round_id`). `improver_instructions.md` is written as goals, describes what the claim agent works from, says a change never names a claim, and wraps `{recipe}`.
  - The tools, with the lab's names and arguments: `read_results()` returns `improver_view`; `propose_change(instructions, model, reasoning_effort, reason)` returns an error to the improver, before any pause, when the model or effort is not allowed; `run_evaluation()` refuses when there is no approved, untested version or the estimate for three runs is more than what's left, then runs the evaluation set once and the held-out set twice through `dispatch`, each in a fresh `contextvars.Context`, records the verdict with `keep_or_roll_back`, makes a kept version current, and returns the scores; `propose_recipe(recipe, reason)`.
  - An approved `propose_change` adds the version (not current); an approved `propose_recipe` adds a recipe version and makes it current. A rejection resumes with the handler's reason as the tool's answer.
- `dispatcher.dispatch(..., budget: Callable[[], bool] | None)`: `work_claim` starts no claim once the round has spent its limit; a run cut short ends `stopped` with "the round's spending limit was reached".
- Service: `POST /rounds` (`start_round`), `POST /rounds/{id}/stop` (`stop_round`), `POST /rounds/{id}/decision` with `{proposal_id, approve, reason, handler_id}` (`decide_proposal`). A round's stretches of work (first evaluations, the improver up to a pause, resuming after a decision) hold the one-run lock; while it waits, it holds nothing. A refusal names what is running: "a dispatch is running", "an evaluation is running", "round 3 is running". A decision is recorded with the handler before the improver resumes; stopping marks the round stopped, starts no more claims or model calls, and lets go of the lock when the work in hand ends. On startup, a round in `first_evaluations`, `improving`, `evaluating` or `verdict` is marked stopped; one in `awaiting_change` or `awaiting_recipe` stays waiting, its checkpoint intact.
- Backend: `POST /rounds`, `POST /rounds/{id}/stop`, `POST /rounds/{id}/decision` (`start_round`, `stop_round`, `decide_proposal`) need `senior` and forward with the handler; `GET /rounds` (`list_rounds`) and `GET /rounds/{id}` (`get_round`) read the records, including a waiting proposal with its diff.
- Langfuse: the improver's calls go in session `round-<id>`; its evaluations' claims carry the tag `round-<id>` beside `run-<id>`.

- [ ] **Step 1: Write the failing tests on a stub improver model, a stub claim model and a stub judge**

```python
def test_a_proposal_changes_nothing_until_it_is_approved(env): ...
def test_a_rejections_reason_reaches_the_improver(env): ...          # the stub sees it as propose_change's answer
def test_a_proposal_outside_the_models_allowed_goes_back_without_a_pause(env): ...
def test_an_approval_while_a_dispatch_runs_is_refused_with_nothing_recorded(service_env): ...
def test_an_evaluation_that_would_go_over_the_limit_is_refused(env): ...
def test_an_evaluation_cut_short_by_the_limit_is_stopped_and_the_version_rolled_back(env): ...   # Review Focus 4
def test_a_round_stopped_before_its_verdict_leaves_the_current_version(env): ...
def test_a_round_stopped_after_its_verdict_keeps_it(env): ...
def test_a_round_runs_the_first_evaluations_it_is_owed(env): ...
def test_a_round_that_proposes_nothing_finishes_with_no_version(env): ...
def test_a_waiting_round_survives_a_restart(service_env): ...        # Review Focus 2
def test_the_claim_agent_still_cannot_reach_the_scores_or_the_decisions(): ...
def test_only_a_senior_handler_can_start_stop_or_decide(client, signed_in_as): ...   # Marcus: 403
@pytest.mark.parametrize("path", ["/rounds", "/rounds/1/stop", "/rounds/1/decision"])
def test_round_actions_need_a_sign_in(client, path): ...             # 401, and PUT /settings/{name} likewise in Task 2
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run all backend tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(improver): an agent that proposes changes, waits for a handler and has them evaluated`.

### Task 7: The Improvement page

**Files:** Create `frontend/src/pages/Improvement.tsx`, `frontend/src/components/ScoreChart.tsx`, `e2e/tests/04-improvement.spec.ts`. Modify `frontend/src/api.ts`, `App.tsx`, `e2e/stand_in_agent.py`. Test `frontend/src/pages/Improvement.test.tsx`, `components/ScoreChart.test.tsx`.

**Interfaces:**
- The open round, polled every 2 seconds without moving the layout: its stage as a stepper (first evaluations "2 of 3", the improver working, a change waiting, evaluations "1 of 3", the verdict, a recipe waiting, finished or stopped), the spending so far against its limit, and Stop round behind a confirmation.
- A waiting proposal: the improver's reason, the model and effort before and after, the `DiffView`, and Approve or Reject; Reject needs a reason. Both behind `ConfirmDialog`. For Marcus and Priya, the buttons are replaced by "Only a handler who can sign off payouts over the limit can approve the improver's proposals."
- Start round, disabled with the reason while a round is open or something is running.
- The rounds so far, newest first: recipe version, version tried, held-out accuracy and grade before and after, the verdict in a pill with why, who approved or rejected what and when, and the cost.
- `ScoreChart`: SVG, held-out accuracy (0–100%) and grade (0–3) for each version tried, kept versions filled and rolled-back ones hollow, with axes and a legend, no library.
- The recipe's versions, each opening its diff.

- [ ] **Step 1: Write the failing tests**: "shows the open round's stage as it happens"; "approves a proposal after the confirmation"; "needs a reason to reject"; "tells Marcus why he can't approve"; "lists the rounds newest first with the verdict"; `ScoreChart` "draws one point per version tried, hollow when rolled back". E2e: Dana starts a round through the API on the scripted improver, the page shows "A change is waiting for approval", she approves it in the dialog, the round reaches the verdict and then the recipe, she approves that, and the round shows finished with its verdict and cost.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `npm run check`.** Expected: green.
- [ ] **Step 5: Commit** `feat(frontend): the Improvement page: rounds as they happen, proposals to approve, the chart and the recipes`.

### Task 8: A dashboard that looks like enterprise software

**Files:** Create `frontend/src/components/PageHeader.tsx`, `StatusPill.tsx`, `ConfirmDialog.tsx`, `States.tsx` (`Loading`, `Empty`, `ErrorState`, each saying what to do next). Modify every page, `App.tsx` (navigation with the current page marked, the handler and Sign out), `styles/tokens.css`, `app.css`. Test the components and one snapshot-free test per page that its header names the page and what it is for.

**Interfaces:**
- Every page starts with `PageHeader` (eyebrow, title, one sentence on what the page is for, actions on the right). One spacing scale and one table style: numbers right-aligned with tabular figures and their units ("$1,550.00", "24.6 s", "88%"). One button style per kind (primary, secondary, danger, ghost). Statuses as `StatusPill`: a color and a word.
- `ConfirmDialog`: the dashboard's own modal (focus kept inside, Escape cancels), used before every action that changes the agent or a setting: approving or rejecting a proposal, starting or stopping a round, changing a setting. Sign-off, dispatch and evaluation keep working as they do today.
- Polling never re-renders a page into a loading state once it has data, and rows keep their height, so nothing jumps.
- Light and dark from the same tokens, by `data-theme` or the computer's preference; every color pair meets `docs/DESIGN.md`. A laptop screen (1280 × 800) needs no sideways scrolling.

- [ ] **Step 1: Write the failing tests**: `ConfirmDialog` "keeps focus inside and cancels on Escape"; `StatusPill` "shows a word with its color"; `States` "says what to do next"; each page "has a header that says what it is for".
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement**, page by page. Nothing on the existing pages changes how it works: the e2e specs pass unchanged apart from selectors.
- [ ] **Step 4: Run `npm run check`.** Expected: green.
- [ ] **Step 5: Commit** `feat(frontend): one header, spacing, tables, buttons and states on every page, in light and dark`.

### Task 9: The documents

**Files:** Modify `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/PROCESS.md`, `docs/CODE_STANDARDS.md`, `docs/STATUS.md`, `data/README.md`, `COMMANDS.md`, `README.md`.

- `SPEC.md`: the approval limit is a setting; the improver reads the evaluation set's decisions and reasons, the claim agent never does and nothing in the agent reads the held-out set's; sign-in and who may do what; the Configuration, Settings and Improvement pages; the judge's grade. The Rules otherwise unchanged.
- `TECH_STACK.md`: Supabase Auth and PyJWT, the `openrouter` package and Jev, the improver, the new environment variables (`VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY`, `HANDLER_PASSWORD`, `OPENROUTER_DECISIONS_URL`), the new routes.
- `PROCESS.md`: the live runs of this brief; the credit limit on the OpenRouter key.
- `CODE_STANDARDS.md`: the six parts of the agent; a "Clients" section (the improver's module, the dashboard's Supabase client); instructions written as goals around the recipe.
- `data/README.md`: `improvement.json`, `models.json`, the handlers' rights, `reference` in the answer key.

- [ ] **Step 1: Write them**, then run each command in `COMMANDS.md` that spends nothing.
- [ ] **Step 2: Commit** `docs: describe sign-in, settings, versions, the judge and the improver`.

### Task 10: Live verification

Needs the owner's `.env` from Task 1. It spends on OpenRouter (the claim agent, the improver and Jev) and writes to Supabase and Langfuse. Estimate: each round evaluates 75 claims, and the first round 75 more for version 1; at about $0.0015 a claim on DeepSeek V4.1 Flash, two rounds come to about $0.35, plus the improver and the judge, plus a dispatch of 25. Aim: under $1.

**Files:** Extend `scripts/check_live.py` (`--round N`: its stages, proposals, verdict, cost; `--session round-N`: the improver's traces). Record results in `docs/STATUS.md`.

- [ ] **Step 1: Prepare.** Check the OpenRouter key's credit limit with the lab's last cell. Close the lab notebooks. Take a baseline of connections through the Supabase MCP server.
- [ ] **Step 2: Reseed once.** `npm run seed`. Expected: 25 claims, configuration and recipe at version 1, no rounds.
- [ ] **Step 3: Two rounds, as Dana Whitfield**, through the Playwright MCP server, signed in with `HANDLER_PASSWORD`. Round 1: the first evaluations run (1, 2, 3 of 3 on the page); reject the first proposal with a reason; approve the next and the recipe. Round 2: approve the change and the recipe. Expected: each stage shows as it happens; afterwards the rounds show the scores before and after, the verdict and the cost; the Configuration page shows the versions and their diffs. Report how far version 1's two held-out runs differed on each measure, beside the noise and tolerance.
- [ ] **Step 4: Langfuse.** `python scripts/check_live.py --session round-1` and `round-2`. Expected: each round's improver traces in one session.
- [ ] **Step 5: A dispatch at N=25** on the current version. Expected: it records that version.
- [ ] **Step 6: Rights.** As Marcus Oyelaran: a held payout can't be signed off and the settings can't be changed, and the page says why. As Dana Whitfield: the same payout is signed off and paid.
- [ ] **Step 7: Screenshots** of every page in light and dark mode. A fresh subagent reviews them once, as a senior product designer would, against `docs/DESIGN.md` and point 8 of the brief; fix what it finds, each fix with a test where it changes behavior.
- [ ] **Step 8: Report the spending** from `model_calls` and `judge_grades`. Expected: under $1.
- [ ] **Step 9: Commit** `docs: record the live verification of the improver`.

## Done means

Each line under "What done looks like" in `docs/IMPROVE.md` has its evidence: tests in Tasks 1 to 8, documents in Task 9, live checks in Task 10.
