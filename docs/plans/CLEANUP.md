# Cleanup and efficiency pass (2026-10-30)

Done after the harness, at the request of the project owner: map the project, then tidy the repo, the backend and the front end, and make the agent cheaper and faster. Status: done. Every step kept `npm run check` green.

## What changed

**Repo**
- Finished plans and the harness brief moved to `docs/plans/`. A stray Code Runner notebook (`tempCodeRunnerFile.ipynb`) was deleted and its pattern added to `.gitignore`. Dead code (`Reference.wording`) removed.
- Not moved: `labs/`, `lab1_desk/`, `lab2_desk/`. Each desk has its own `.venv`, and the notebooks use relative paths (`Path("data")`, `Path("../data")`). Moving them would break both, for a cosmetic gain. The README now says they are course material, not part of the app.

**Backend**
- `api.py`: one connection dependency instead of ten `try/finally` blocks. `POST /runs` with an empty `claim_ids` list is a 422. "What the agent read" lists only sections actually read, not lookups refused by the cap or rereads.
- One `run_graph` loop shared by the claim agent and the orchestrator. The scripted stand-ins live in `app/scripted.py`.
- One section index per app (`documents_for`). To-do changes made by `work_claim` are recorded as run events.
- `Reference.policy` and `Reference.customer` raise a clear `LookupError` for an unknown id.

**Agent efficiency**
- The claim agent gets the claim, policy, plan and customer history as results of tool calls it did not have to make, so customer text still arrives only inside tool results. The section index of both documents is in its prompt, and `list_sections` is no longer offered.
- Lean claim instructions, no rereads of a section, at most 8 sections per claim (done just before this pass).
- The instructions page shows the prompt the claim agent actually receives, section index included.

**Front end**
- One `usePolling` hook instead of four copies. The claim page no longer polls the latest run; a second run is refused by the API and the page says so. `oxlint --deny-warnings` is part of `npm run check`.

## Measurement

Same five claims (CLM-1001 to CLM-1005), `deepseek/deepseek-v4.1-flash`, low effort, separate scratch databases.

| | Before (lean instructions only) | After (facts up front, section index) |
|---|---|---|
| Cost of the run | $0.0108 | $0.0078 (−28%) |
| Time | 1 min 41 s | 41 s (−60%) |
| Model calls | 55 | 34 |
| Input tokens | 206,671 | 117,569 (−43%) |

For scale, the first live run of the harness, before any of this, cost $0.027 and took 8 minutes for five other claims.

Four of the five decisions were the same in both runs. CLM-1002 was referred in the first and approved for $580.00 in the second. Accuracy was not scored in this pass: run `scripts/score.py --split queue --run --tiny` to check it against the handlers' decisions.
