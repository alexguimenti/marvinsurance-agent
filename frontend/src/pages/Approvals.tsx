import { useCallback, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, approvePayment, listApprovals, type Approval } from '../api'
import { useAuth } from '../auth/context'
import { formatUsd, humanize } from '../format'
import { usePolling } from '../usePolling'
import PageHeader from '../components/PageHeader'
import { Empty, ErrorState } from '../components/States'

export default function Approvals() {
  const [approvals, setApprovals] = useState<Approval[] | null>(null)
  const { handler } = useAuth()
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setApprovals(await listApprovals())
    } catch {
      setError('Could not load the approvals.')
    }
  }, [])

  usePolling(load, null)

  async function onApprove(paymentId: string) {
    setError(null)
    try {
      await approvePayment(paymentId)
      await load()
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 403
          ? 'You are not allowed to approve this payment.'
          : 'Could not approve the payment.',
      )
    }
  }

  return (
    <section>
      <PageHeader
        eyebrow="Sign-off"
        title="Approvals"
        lede="Payouts above $1,000 wait here until a handler signs them off. The agent then pays them."
        actions={handler.can_approve_over_limit && <p className="muted small">Signing off as {handler.name} ({handler.role})</p>}
      />
      {!handler.can_approve_over_limit && (
        <p className="notice" role="note">
          You can't sign off payouts over the limit, so you can't approve these payments. They wait for a handler who can.
        </p>
      )}
      {error && <ErrorState>{error}</ErrorState>}
      {approvals && approvals.length === 0 && <Empty>Nothing is waiting for approval.</Empty>}
      {approvals && approvals.length > 0 && (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>Claim</th>
                <th>Customer</th>
                <th>Type</th>
                <th>Why it waits</th>
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
                  <td className="small">{a.hold_reason === 'gateway_refused' ? 'The Gateway refused this payment' : 'Over the approval limit'}</td>
                  <td className="small">{a.rationale}</td>
                  <td className="num mono amount">{formatUsd(a.amount)}</td>
                  <td className="num">
                    {handler.can_approve_over_limit && (
                      <button className="btn btn-accent" onClick={() => onApprove(a.payment_id)}>
                        Approve payment
                      </button>
                    )}
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
