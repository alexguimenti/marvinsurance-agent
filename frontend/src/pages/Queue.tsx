import { useCallback, useState, type CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, getLatestDispatch, listClaims, startDispatch, type ClaimSummary, type LatestDispatch } from '../api'
import StatusBadge from '../components/StatusBadge'
import RunPanel from '../components/RunPanel'
import { DEFAULT_WORKERS, MAX_WORKERS, MIN_WORKERS, STATUS_LABEL, formatUsd, humanize, runRefused, stampFor } from '../format'
import { usePolling } from '../usePolling'
import PageHeader from '../components/PageHeader'
import { ErrorState } from '../components/States'

const POLL_MS = 2000
const STATUS_ORDER = ['waiting', 'in_progress', 'awaiting_approval', 'decided', 'needs_handler'] as const

export default function Queue() {
  const [claims, setClaims] = useState<ClaimSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [latest, setLatest] = useState<LatestDispatch | null>(null)
  const [workers, setWorkers] = useState(String(DEFAULT_WORKERS))

  const load = useCallback(async () => {
    try {
      const [list, run] = await Promise.all([listClaims(), getLatestDispatch()])
      setClaims(list)
      setLatest(run)
      setError(null)
    } catch {
      setError('Could not load the claims queue.')
    }
  }, [])

  usePolling(load, POLL_MS)

  const n = Number(workers)
  const validN = Number.isInteger(n) && n >= MIN_WORKERS && n <= MAX_WORKERS

  async function onStart() {
    setStarting(true)
    try {
      await startDispatch(n)
      await load()
    } catch (e) {
      setError(runRefused(e instanceof ApiError ? e.status : undefined, 'Could not start the dispatch.'))
    } finally {
      setStarting(false)
    }
  }

  const running = latest?.run?.status === 'running'
  // claims in progress are carried on, so there is work even with nothing waiting
  const work = (claims ?? []).some((c) => c.status === 'waiting' || c.status === 'in_progress')

  const counts = (claims ?? []).reduce<Record<string, number>>((acc, c) => {
    acc[c.status] = (acc[c.status] ?? 0) + 1
    return acc
  }, {})

  return (
    <section>
      <PageHeader
        eyebrow="Claims desk"
        title="Claims queue"
        lede={<>{claims ? `${claims.length} claims on the desk` : 'Loading the desk'}. A dispatch carries on every claim in progress
            and works every waiting one, oldest first, with as many claim agents at once as you choose.</>}
        actions={<div className="run-controls">
          <label className="limit">
            <span>Claim agents at once</span>
            <input
              type="number"
              min={MIN_WORKERS}
              max={MAX_WORKERS}
              inputMode="numeric"
              value={workers}
              onChange={(e) => setWorkers(e.target.value)}
            />
          </label>
          <button className="btn btn-primary" onClick={onStart} disabled={starting || running || !work || !validN}>
            Start dispatch
          </button>
        </div>}
      />

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

      {error && <ErrorState>{error}</ErrorState>}
      {latest && <RunPanel data={latest} />}
      {claims && (
        <div className="card">
          <table className="queue">
            <thead>
              <tr>
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
