import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, approvePayment, listApprovals, listHandlers, type Approval, type Handler } from '../api'
import { formatUsd, humanize } from '../format'
import { usePolling } from '../usePolling'

export default function Approvals() {
  const [approvals, setApprovals] = useState<Approval[] | null>(null)
  const [handlers, setHandlers] = useState<Handler[]>([])
  const [handlerId, setHandlerId] = useState('')
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setApprovals(await listApprovals())
    } catch {
      setError('Could not load the approvals.')
    }
  }, [])

  usePolling(load, null)

  useEffect(() => {
    listHandlers()
      .then((h) => {
        setHandlers(h)
        setHandlerId((current) => current || h[0]?.handler_id || '')
      })
      .catch(() => setError('Could not load the handlers.'))
  }, [])

  async function onApprove(paymentId: string) {
    setError(null)
    try {
      await approvePayment(paymentId, handlerId)
      await load()
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 403
          ? 'That handler is not allowed to approve this payment.'
          : 'Could not approve the payment.',
      )
    }
  }

  return (
    <section>
      <div className="page-head">
        <div>
          <p className="eyebrow">Sign-off</p>
          <h1>Approvals</h1>
          <p className="lede">Payouts above $1,000 wait here until a handler signs them off. The agent then pays them.</p>
        </div>
        <div className="row">
          <label htmlFor="handler">Approve as</label>
          <select id="handler" value={handlerId} onChange={(e) => setHandlerId(e.target.value)}>
            {handlers.map((h) => (
              <option key={h.handler_id} value={h.handler_id}>
                {h.name} ({h.role})
              </option>
            ))}
          </select>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {approvals && approvals.length === 0 && <p className="empty">Nothing is waiting for approval.</p>}
      {approvals && approvals.length > 0 && (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>Claim</th>
                <th>Customer</th>
                <th>Type</th>
                <th>Rationale</th>
                <th className="num">Amount</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {approvals.map((a) => (
                <tr key={a.payment_id}>
                  <td>
                    <Link className="case-id" to={`/claims/${a.claim_id}`}>
                      {a.claim_id}
                    </Link>
                  </td>
                  <td>{a.customer_name}</td>
                  <td>{humanize(a.claim_type)}</td>
                  <td className="small">{a.rationale}</td>
                  <td className="num mono amount">{formatUsd(a.amount)}</td>
                  <td className="num">
                    <button className="btn btn-accent" onClick={() => onApprove(a.payment_id)} disabled={!handlerId}>
                      Approve payment
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
