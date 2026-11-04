import { useCallback, useState, type CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, getLatestRun, listClaims, startRun, type ClaimSummary, type LatestRun } from '../api'
import StatusBadge from '../components/StatusBadge'
import TodoPanel from '../components/TodoPanel'
import { STATUS_LABEL, formatUsd, humanize, runRefused, stampFor } from '../format'
import { usePolling } from '../usePolling'

const POLL_MS = 2000
const STATUS_ORDER = ['waiting', 'in_progress', 'awaiting_approval', 'decided', 'needs_handler'] as const

export default function Queue() {
  const [claims, setClaims] = useState<ClaimSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [latest, setLatest] = useState<LatestRun | null>(null)
  const [limit, setLimit] = useState('')

  const load = useCallback(async () => {
    try {
      const [list, run] = await Promise.all([listClaims(), getLatestRun()])
      setClaims(list)
      setLatest(run)
      // drop ticks on claims the agent has already picked up
      setSelected((prev) => {
        const waiting = new Set(list.filter((c) => c.status === 'waiting').map((c) => c.claim_id))
        const kept = [...prev].filter((id) => waiting.has(id))
        return kept.length === prev.size ? prev : new Set(kept)
      })
      setError(null)
    } catch {
      setError('Could not load the claims queue.')
    }
  }, [])

  usePolling(load, POLL_MS)

  async function onRunQueue() {
    setStarting(true)
    try {
      // a blank limit means every waiting claim; at least 1, so a stopped run can still be finished
      await startRun(selected.size ? { claimIds: [...selected] } : { limit: Number(limit) || Math.max(waitingIds.length, 1) })
      setSelected(new Set())
      await load()
    } catch (e) {
      setError(runRefused(e instanceof ApiError ? e.status : undefined, 'Could not start the run.'))
    } finally {
      setStarting(false)
    }
  }

  const running = latest?.run?.alive === true
  // a stopped claim or run has work left even with nothing waiting
  const unfinished = (claims ?? []).some((c) => c.status === 'in_progress') || latest?.run?.status === 'running'
  const waitingIds = (claims ?? []).filter((c) => c.status === 'waiting').map((c) => c.claim_id)
  const allTicked = waitingIds.length > 0 && waitingIds.every((id) => selected.has(id))

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (!next.delete(id)) next.add(id)
      return next
    })
  }

  const counts = (claims ?? []).reduce<Record<string, number>>((acc, c) => {
    acc[c.status] = (acc[c.status] ?? 0) + 1
    return acc
  }, {})

  return (
    <section>
      <div className="page-head">
        <div>
          <p className="eyebrow">Claims desk</p>
          <h1>Claims queue</h1>
          <p className="lede">
            {claims ? `${claims.length} claims on the desk` : 'Loading the desk'}. Tick the ones you want the agent to take,
            or run the queue, with a limit to try it on a few.
          </p>
        </div>
        <div className="run-controls">
          {!selected.size && (
            <label className="limit">
              <span>Limit</span>
              <input
                type="number"
                min={1}
                inputMode="numeric"
                placeholder="all"
                value={limit}
                onChange={(e) => setLimit(e.target.value)}
              />
            </label>
          )}
          <button className="btn btn-primary" onClick={onRunQueue} disabled={starting || running || (!counts.waiting && !unfinished)}>
            {selected.size ? `Run selected (${selected.size})` : 'Run queue'}
          </button>
        </div>
      </div>

      {claims && (
        <div className="tiles">
          {STATUS_ORDER.map((status) => (
            <div key={status} className={`tile tile-${status}`} role="group" aria-label={STATUS_LABEL[status]}>
              <span className="tile-n">{counts[status] ?? 0}</span>
              <span className="tile-l">{STATUS_LABEL[status]}</span>
            </div>
          ))}
        </div>
      )}

      {error && <p className="error">{error}</p>}
      {latest && <TodoPanel run={latest.run} todo={latest.todo} />}
      {claims && (
        <div className="card">
          <table className="queue">
            <thead>
              <tr>
                <th className="check">
                  <input
                    type="checkbox"
                    aria-label="Select all waiting claims"
                    checked={allTicked}
                    disabled={waitingIds.length === 0}
                    onChange={() => setSelected(allTicked ? new Set() : new Set(waitingIds))}
                  />
                </th>
                <th>Claim</th>
                <th>Customer</th>
                <th>Type</th>
                <th className="num">Claimed</th>
                <th>Status</th>
                <th>Decision</th>
                <th className="num">Payout</th>
              </tr>
            </thead>
            <tbody>
              {claims.map((c, i) => (
                <tr key={c.claim_id} style={{ '--i': i } as CSSProperties}>
                  <td className="check">
                    {c.status === 'waiting' && (
                      <input
                        type="checkbox"
                        aria-label={`Select ${c.claim_id}`}
                        checked={selected.has(c.claim_id)}
                        onChange={() => toggle(c.claim_id)}
                      />
                    )}
                  </td>
                  <td>
                    <Link className="case-id" to={`/claims/${c.claim_id}`}>
                      {c.claim_id}
                    </Link>
                    <div className="muted small">{c.summary}</div>
                    {c.status === 'waiting' && c.attempts > 0 && (
                      <div className="retry-note small">
                        {c.attempts} failed attempt{c.attempts === 1 ? '' : 's'}, back in the queue
                      </div>
                    )}
                  </td>
                  <td>{c.customer_name}</td>
                  <td className="muted">{humanize(c.claim_type)}</td>
                  <td className="num mono">{formatUsd(c.amount_claimed)}</td>
                  <td>
                    <StatusBadge status={c.status} />
                  </td>
                  <td>
                    {c.decision ? (
                      <span className={`stamp stamp-${stampFor(c.status, c.decision).key}`}>
                        {stampFor(c.status, c.decision).label}
                      </span>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td className="num mono">{c.payout ? formatUsd(c.payout) : <span className="muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
