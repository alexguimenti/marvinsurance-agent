"""Runs of the agent over the queue: the run record, its to-do list, its events and its checks."""
import json
import sqlite3

TODO_STATUSES = ("todo", "in_progress", "done", "left_for_handler")
DECIDED = ("decided", "awaiting_approval")


class RunActive(Exception):
    """Another run is still going."""


def start_run(conn: sqlite3.Connection, model: str, cap: int | None = None, scope: list[str] | None = None) -> int:
    started = conn.execute(
        "INSERT INTO runs (status, cap, scope, model) "
        "SELECT 'running', ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM runs WHERE status = 'running')",
        (cap, json.dumps(scope) if scope is not None else None, model),
    )
    conn.commit()
    if started.rowcount == 0:
        raise RunActive("a run is already in progress")
    return started.lastrowid


def get_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()


def run_event(conn: sqlite3.Connection, run_id: int, kind: str, content: dict) -> None:
    conn.execute(
        "INSERT INTO run_events (run_id, seq, kind, content) "
        "SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ? FROM run_events WHERE run_id = ?",
        (run_id, kind, json.dumps(content), run_id),
    )
    conn.commit()


def run_events(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute("SELECT seq, kind, content, created_at FROM run_events WHERE run_id = ? ORDER BY seq", (run_id,))
    return [{"seq": r["seq"], "kind": r["kind"], "content": json.loads(r["content"]), "created_at": r["created_at"]}
            for r in rows]


# --- the to-do list ---------------------------------------------------------

def todo_list(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT item_id, position, title, status, claim_id, note FROM todo_items WHERE run_id = ? ORDER BY position",
        (run_id,),
    )
    return [dict(r) for r in rows]


def set_todo(conn: sqlite3.Connection, run_id: int, items: list[dict]) -> list[dict]:
    """Replace the run's plan. Each item is {"title": ..., "claim_id": ... or None}."""
    conn.execute("DELETE FROM todo_items WHERE run_id = ?", (run_id,))
    for position, item in enumerate(items, start=1):
        conn.execute(
            "INSERT INTO todo_items (run_id, position, title, status, claim_id) VALUES (?, ?, ?, 'todo', ?)",
            (run_id, position, item["title"], item.get("claim_id")),
        )
    conn.commit()
    run_event(conn, run_id, "todo", {"action": "plan", "items": len(items)})
    return todo_list(conn, run_id)


def update_todo(conn: sqlite3.Connection, run_id: int, item_id: int, status: str, note: str | None = None) -> dict:
    if status not in TODO_STATUSES:
        raise ValueError(f"unknown status {status!r}; use one of {', '.join(TODO_STATUSES)}")
    item = conn.execute("SELECT * FROM todo_items WHERE run_id = ? AND item_id = ?", (run_id, item_id)).fetchone()
    if item is None:
        raise ValueError(f"there is no item {item_id} in this run's list")
    if status == "done" and item["claim_id"]:
        claim = conn.execute("SELECT status FROM claims WHERE claim_id = ?", (item["claim_id"],)).fetchone()
        if claim is None or claim["status"] not in DECIDED:
            raise ValueError(f"{item['claim_id']} is not decided yet, so its item cannot be done")
    conn.execute("UPDATE todo_items SET status = ?, note = COALESCE(?, note) WHERE item_id = ?", (status, note, item_id))
    conn.commit()
    run_event(conn, run_id, "todo", {"action": "update", "item_id": item_id, "status": status})
    return dict(conn.execute("SELECT * FROM todo_items WHERE item_id = ?", (item_id,)).fetchone())


def todo_for_claim(conn: sqlite3.Connection, run_id: int, claim_id: str, status: str, note: str | None = None) -> None:
    """Keep a claim's item in step with the claim, adding one if the plan left it out."""
    item = conn.execute(
        "SELECT item_id FROM todo_items WHERE run_id = ? AND claim_id = ? ORDER BY position LIMIT 1", (run_id, claim_id)
    ).fetchone()
    if item is None:
        conn.execute(
            "INSERT INTO todo_items (run_id, position, title, status, claim_id, note) "
            "SELECT ?, COALESCE(MAX(position), 0) + 1, ?, ?, ?, ? FROM todo_items WHERE run_id = ?",
            (run_id, f"Work {claim_id}", status, claim_id, note, run_id),
        )
    else:
        conn.execute("UPDATE todo_items SET status = ?, note = ? WHERE item_id = ?", (status, note, item["item_id"]))
    conn.commit()


# --- scope, cap and the missed-claim check -------------------------------------

def _scope(conn: sqlite3.Connection, run_id: int) -> list[str] | None:
    scope = get_run(conn, run_id)["scope"]
    return json.loads(scope) if scope is not None else None


def in_scope_waiting(conn: sqlite3.Connection, run_id: int) -> list[str]:
    waiting = [r[0] for r in conn.execute("SELECT claim_id FROM claims WHERE status = 'waiting' ORDER BY claim_id")]
    scope = _scope(conn, run_id)
    return waiting if scope is None else [c for c in waiting if c in set(scope)]


def in_scope(conn: sqlite3.Connection, run_id: int, claim_id: str) -> bool:
    scope = _scope(conn, run_id)
    return scope is None or claim_id in scope


def taken_claims(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT claim_id, status FROM claims WHERE run_id = ? ORDER BY claim_id", (run_id,)).fetchall()


def cap_reached(conn: sqlite3.Connection, run_id: int) -> bool:
    cap = get_run(conn, run_id)["cap"]
    return cap is not None and len(taken_claims(conn, run_id)) >= cap


def missed_claims(conn: sqlite3.Connection, run_id: int) -> list[str]:
    """Claims the run still owes: its own claims back in the queue or still in progress,
    then untried waiting claims in scope, as many as the cap still allows."""
    taken = taken_claims(conn, run_id)
    owed = [r["claim_id"] for r in taken if r["status"] in ("waiting", "in_progress")]
    cap = get_run(conn, run_id)["cap"]
    room = None if cap is None else max(cap - len(taken), 0)
    taken_ids = {r["claim_id"] for r in taken}
    untried = [c for c in in_scope_waiting(conn, run_id) if c not in taken_ids]
    return owed + (untried if room is None else untried[:room])


# --- ending a run ------------------------------------------------------------

def sum_usage(usages: list[dict]) -> dict | None:
    if not usages:
        return None
    costs = [u["cost_usd"] for u in usages if u.get("cost_usd") is not None]
    total = {key: sum(u.get(key, 0) for u in usages)
             for key in ("calls", "input_tokens", "output_tokens", "reasoning_tokens")}
    total["cost_usd"] = round(sum(costs), 8) if costs else None
    return total


def run_usage(conn: sqlite3.Connection, run_id: int) -> dict | None:
    """The orchestrator's usage plus every claim attempt made in the run."""
    rows = conn.execute(
        "SELECT content FROM run_events WHERE run_id = ? AND kind = 'usage' "
        "UNION ALL SELECT content FROM trace_events WHERE run_id = ? AND kind = 'usage'",
        (run_id, run_id),
    )
    return sum_usage([json.loads(r[0]) for r in rows])


def finish(conn: sqlite3.Connection, run_id: int, status: str, summary: str | None = None, error: str | None = None) -> None:
    conn.execute("UPDATE claims SET status = 'waiting' WHERE run_id = ? AND status = 'in_progress'", (run_id,))
    usage = run_usage(conn, run_id)
    conn.execute(
        "UPDATE runs SET status = ?, summary = COALESCE(?, summary), error = ?, usage = ?, "
        "finished_at = CURRENT_TIMESTAMP WHERE run_id = ?",
        (status, summary, error, json.dumps(usage) if usage else None, run_id),
    )
    conn.commit()


def recover_after_restart(conn: sqlite3.Connection) -> None:
    """A server that stopped mid-run leaves a run 'running' and claims 'in_progress': undo both."""
    for row in conn.execute("SELECT claim_id FROM claims WHERE status = 'in_progress'").fetchall():
        conn.execute("UPDATE claims SET status = 'waiting' WHERE claim_id = ?", (row["claim_id"],))
        conn.execute(
            "INSERT INTO trace_events (claim_id, seq, kind, content) "
            "SELECT ?, COALESCE(MAX(seq), 0) + 1, 'status', ? FROM trace_events WHERE claim_id = ?",
            (row["claim_id"], json.dumps({"status": "waiting", "note": "recovered after a restart"}), row["claim_id"]),
        )
    conn.commit()
    for row in conn.execute("SELECT run_id FROM runs WHERE status = 'running'").fetchall():
        finish(conn, row["run_id"], "failed", error="the server stopped during the run")
