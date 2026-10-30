# lab1_desk: claims handler agent

An agent built on the Anthropic API SDK (tool runner) that decides claims for Marvin FartInsurance.

```
claims/queue/      the claims to work (one file per claim)
claims/held_out/   a second set, for checking afterwards
customers/         one file per customer
policies/          policies.json, plans.json, company.json, policy_wording.md, claims_manual.md
decisions/         the agent writes decisions/<claim id>.json here
tasks/             the agent's task list per claim, tasks/<claim id>.md
handover.md        written when every claim has a decision
claims_handler.py  the agent
```

## Run

From this folder, with the key in the repo's `.env` (`ANTHROPIC_API_KEY`):

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

.venv\Scripts\python.exe claims_handler.py                       # the whole queue
.venv\Scripts\python.exe claims_handler.py --claim CLM-1001      # one claim
.venv\Scripts\python.exe claims_handler.py --set held_out --limit 5
.venv\Scripts\python.exe claims_handler.py --model claude-sonnet-5-5 --effort medium
```

Claims that already have a decision are skipped; add `--force` to redo them. To start clean, delete the files in `decisions/` (keep `.gitkeep`) and `tasks/`.

## How it works

Each claim gets its own agent run. The agent starts from a task list, reads the claim, customer, policy, wording and manual through tools, keeps the task list up to date, and records the decision with `write_decision` (approve, decline or refer). The tool checks the payout: 0 unless approved, and never above the amount claimed. The agent can only read this folder and only write the files of the claim it was given.

The human decisions in `data/evals/` are not copied here, so the agent cannot see them.
