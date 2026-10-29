import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { listClaims, runQueue, type ClaimSummary } from '../api'
import StatusBadge from '../components/StatusBadge'
import { DECISION_LABEL, formatUsd, humanize } from '../format'

const POLL_MS = 2000

export default function Queue() {
  const [claims, setClaims] = useState<ClaimSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)

  const load = useCallback(async () => {
    try {
      setClaims(await listClaims())
      setError(null)
    } catch {
      setError('Could not load the claims queue.')
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = setInterval(() => void load(), POLL_MS)
    return () => clearInterval(timer)
  }, [load])

  async function onRunQueue() {
    setStarting(true)
    try {
      await runQueue()
      await load()
    } catch {
      setError('Could not start the queue.')
    } finally {
      setStarting(false)
    }
  }

  const counts = (claims ?? []).reduce<Record<string, number>>((acc, c) => {
    acc[c.status] = (acc[c.status] ?? 0) + 1
    return acc
  }, {})

  return (
    <section>
      <div className="page-head">
        <div>
          <h1>Claims queue</h1>
          <p className="muted">
            {claims ? `${claims.length} claims` : 'Loading'}
            {counts.waiting ? ` · ${counts.waiting} waiting` : ''}
            {counts.awaiting_approval ? ` · ${counts.awaiting_approval} awaiting approval` : ''}
          </p>
        </div>
        <button className="btn btn-primary" onClick={onRunQueue} disabled={starting || !counts.waiting}>
          Run queue
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {claims && (
        <div className="card">
          <table>
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
              {claims.map((c) => (
                <tr key={c.claim_id}>
                  <td>
                    <Link to={`/claims/${c.claim_id}`}>{c.claim_id}</Link>
                    <div className="muted small">{c.summary}</div>
                  </td>
                  <td>{c.customer_name}</td>
                  <td>{humanize(c.claim_type)}</td>
                  <td className="num mono">{formatUsd(c.amount_claimed)}</td>
                  <td>
                    <StatusBadge status={c.status} />
                  </td>
                  <td>{c.decision ? DECISION_LABEL[c.decision] : <span className="muted">—</span>}</td>
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
