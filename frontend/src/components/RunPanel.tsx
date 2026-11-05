import { Link } from 'react-router-dom'
import type { LatestDispatch } from '../api'
import { RUN_LABEL, STATUS_LABEL, formatCost, formatSeconds, formatTime, formatUsd } from '../format'

/** The latest dispatch: its claims and where each stands, its cost and time, and what it came to. */
export default function RunPanel({ data }: { data: LatestDispatch }) {
  const { run, claims, totals, summary } = data
  if (!run) {
    return (
      <div className="card pad run-panel">
        <h3>Dispatch</h3>
        <p className="muted">No dispatch yet. Start one and its claims appear here.</p>
      </div>
    )
  }
  const done = claims.filter((c) => c.finished_at).length
  return (
    <div className={`card pad run-panel run-${run.status}`}>
      <div className="todo-head">
        <h3>Dispatch</h3>
        <p className="muted small">
          Dispatch {run.run_id} · <span className={`run-status run-status-${run.status}`}>{RUN_LABEL[run.status]}</span>
          {` · ${run.workers} claim agent${run.workers === 1 ? '' : 's'} at once`}
          {claims.length > 0 && ` · ${done} of ${claims.length} done`}
        </p>
        {totals && (
          <p className="muted small run-totals">
            {totals.cost_usd === null ? 'cost not reported' : `${formatCost(totals.cost_usd)} in model calls`}
            {` · ${formatSeconds(totals.seconds)} of agent time`}
          </p>
        )}
      </div>
      {run.status === 'stopped' && (
        <p className="muted">This dispatch was stopped. The next dispatch carries on its claims in progress.</p>
      )}
      {claims.length === 0 && run.status === 'running' && <p className="muted">Listing the claims.</p>}
      {claims.length > 0 && (
        <ol className="todo run-claims">
          {claims.map((c) => (
            <li key={c.claim_id} className={`todo-item run-claim run-claim-${c.status}`}>
              <span className="todo-mark" aria-hidden="true" />
              <Link className="case-id" to={`/claims/${c.claim_id}`}>
                {c.claim_id}
              </Link>
              <span className="todo-status">{STATUS_LABEL[c.status] ?? c.status}</span>
              <span className="todo-note">
                {c.started_at ? `started ${formatTime(c.started_at)}` : 'not started'}
                {c.seconds > 0 && ` · ${formatSeconds(c.seconds)}`}
              </span>
            </li>
          ))}
        </ol>
      )}
      {summary && (
        <div className="run-summary">
          <p>
            {summary.approved} approved · {summary.declined} declined · {summary.referred} referred
          </p>
          {summary.awaiting_sign_off.length > 0 && (
            <div>
              <h4>Waiting for a sign-off</h4>
              <ul>
                {summary.awaiting_sign_off.map((p) => (
                  <li key={p.claim_id}>
                    <Link to={`/claims/${p.claim_id}`}>{p.claim_id}</Link> <span className="mono">{formatUsd(p.payout)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {summary.left_for_handler.length > 0 && (
            <div>
              <h4>Left for a handler</h4>
              <ul>
                {summary.left_for_handler.map((id) => (
                  <li key={id}>
                    <Link to={`/claims/${id}`}>{id}</Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {summary.in_progress.length > 0 && (
            <div>
              <h4>Still in progress, for the next dispatch</h4>
              <ul>
                {summary.in_progress.map((id) => (
                  <li key={id}>
                    <Link to={`/claims/${id}`}>{id}</Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
      {run.error && <p className="error small">The dispatch stopped: {run.error}</p>}
    </div>
  )
}
