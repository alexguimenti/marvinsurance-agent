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
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const load = useCallback(async () => {
    try {
      const list = await listClaims()
      setClaims(list)
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

  useEffect(() => {
    void load()
    const timer = setInterval(() => void load(), POLL_MS)
    return () => clearInterval(timer)
  }, [load])

  async function onRunQueue() {
    setStarting(true)
    try {
      await runQueue(selected.size ? [...selected] : undefined)
      setSelected(new Set())
      await load()
    } catch {
      setError('Could not start the queue.')
    } finally {
      setStarting(false)
    }
  }

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
          <h1>Claims queue</h1>
          <p className="muted">
            {claims ? `${claims.length} claims` : 'Loading'}
            {counts.waiting ? ` · ${counts.waiting} waiting` : ''}
            {counts.awaiting_approval ? ` · ${counts.awaiting_approval} awaiting approval` : ''}
          </p>
        </div>
        <button className="btn btn-primary" onClick={onRunQueue} disabled={starting || !counts.waiting}>
          {selected.size ? `Run selected (${selected.size})` : 'Run queue'}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {claims && (
        <div className="card">
          <table>
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
              {claims.map((c) => (
                <tr key={c.claim_id}>
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
