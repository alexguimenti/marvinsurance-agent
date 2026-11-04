import type { Run, TodoItem } from '../api'
import { RUN_LABEL, TODO_LABEL, formatCost } from '../format'

/** The agent's to-do list for the current (or latest) run, ticked off as it works. */
export default function TodoPanel({ run, todo }: { run: Run | null; todo: TodoItem[] }) {
  if (!run) {
    return (
      <div className="card pad todo-panel">
        <h3>Agent's to-do list</h3>
        <p className="muted">No run yet. Start one and the agent's plan appears here.</p>
      </div>
    )
  }
  const done = todo.filter((i) => i.status === 'done').length
  return (
    <div className={`card pad todo-panel run-${run.status}`}>
      <div className="todo-head">
        <h3>Agent's to-do list</h3>
        <p className="muted small">
          Run {run.run_id} · <span className={`run-status run-status-${run.status}`}>{RUN_LABEL[run.status]}</span>
          {run.cap !== null && ` · capped at ${run.cap}`}
          {todo.length > 0 && ` · ${done} of ${todo.length} done`}
          {run.usage?.cost_usd != null && ` · ${formatCost(run.usage.cost_usd)}`}
        </p>
      </div>
      {todo.length === 0 && run.alive && <p className="muted">The agent is planning.</p>}
      {run.status === 'running' && !run.alive && (
        <p className="muted">This run was stopped. The next run finishes it, with its own limit and claims.</p>
      )}
      {todo.length > 0 && (
        <ol className="todo">
          {todo.map((item) => (
            <li key={item.item_id} className={`todo-item todo-${item.status}`}>
              <span className="todo-mark" aria-hidden="true" />
              <span className="todo-title">{item.title}</span>
              <span className="todo-status">{TODO_LABEL[item.status]}</span>
              {item.note && <span className="todo-note">{item.note}</span>}
            </li>
          ))}
        </ol>
      )}
      {run.summary && <p className="todo-summary">{run.summary}</p>}
      {run.status === 'failed' && run.error && <p className="error small">The run stopped: {run.error}</p>}
    </div>
  )
}
