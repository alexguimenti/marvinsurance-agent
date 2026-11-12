import { useCallback, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, approvePayment, getClaim, sendBack, type ClaimDetailData } from '../api'
import { useAuth } from '../auth/context'
import StatusBadge from '../components/StatusBadge'
import { capitalize, describeLookup, describeUsage, formatTime, formatUsd, humanize, stampFor } from '../format'
import { usePolling } from '../usePolling'
import Trace from './Trace'
import PageHeader from '../components/PageHeader'
import { ErrorState, Loading } from '../components/States'

const MAX_NOTE = 1000
const POLL_MS = 2000
const POLL_WORKING_MS = 1000

export default function ClaimDetail() {
  const { id = '' } = useParams()
  const [data, setData] = useState<ClaimDetailData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { handler } = useAuth()
  const [sending, setSending] = useState(false) // the Send back form is open
  const [note, setNote] = useState('')

  async function answer(what: () => Promise<unknown>, failed: string) {
    setError(null)
    try {
      await what()
      setSending(false)
      setNote('')
      setData(await getClaim(id))
    } catch (e) {
      setError(e instanceof ApiError && e.status === 409 ? 'This claim is no longer waiting for a sign-off.' : failed)
    }
  }

  const load = useCallback(async () => {
    try {
      setData(await getClaim(id))
      setError(null)
    } catch {
      setError('Could not load this claim.')
    }
  }, [id])

  // quicker while the agent is at it: a signed-off claim should show as paid within seconds
  usePolling(load, data?.status === 'in_progress' ? POLL_WORKING_MS : POLL_MS)

  if (error && !data) return <ErrorState>{error}</ErrorState>
  if (!data) return <Loading what="the page" />

  const { claim, decision, payment } = data
  return (
    <section>
      <p className="crumb">
        <Link to="/">← Claims queue</Link>
      </p>
      <PageHeader
        eyebrow="Case file"
        title={claim.claim_id}
        lede={<>{humanize(claim.claim_type)} · {data.customer.name} · {claim.incident_location} · incident{' '}
            {claim.incident_date}</>}
        actions={<div className="row">
          <StatusBadge status={data.status} />
        </div>}
      />
      {error && <ErrorState>{error}</ErrorState>}

      {data.copy_of && (
        <div className="notice notice-copy">
          Evaluation copy of <Link to={`/claims/${data.copy_of}`}>{data.copy_of}</Link>, worked in{' '}
          <Link to={`/scoreboard/${data.evaluation_id}`}>evaluation {data.evaluation_id}</Link>. Nobody signs off a copy: a
          held approval counts as finished.
        </div>
      )}

      {data.status === 'awaiting_approval' && payment && !data.copy_of && (
        <div className="notice">
          Held for approval:{' '}
          {payment.hold_reason === 'gateway_refused'
            ? `the Gateway refused the payment of ${formatUsd(payment.amount)}, so it waits for a handler.`
            : `the payout of ${formatUsd(payment.amount)} is above the $1,000 limit and waits for a handler.`}{' '}
          <Link to="/approvals">Go to approvals</Link>
          {handler.can_approve_over_limit && payment && (
            <div className="review">
              <div className="row">
                <button className="btn btn-accent" onClick={() => answer(() => approvePayment(payment.payment_id), 'Could not sign off the payment.')}>
                  Sign off
                </button>
                <button className="btn btn-secondary" aria-expanded={sending} onClick={() => setSending(!sending)}>
                  Send back
                </button>
              </div>
              {sending && (
                <form
                  className="send-back"
                  onSubmit={(e) => {
                    e.preventDefault()
                    void answer(() => sendBack(id, note.trim()), 'Could not send the claim back.')
                  }}
                >
                  <label>
                    Note for the agent
                    <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} />
                  </label>
                  <p className={note.length > MAX_NOTE ? 'small error-text' : 'small muted'}>
                    {note.length > MAX_NOTE ? `${MAX_NOTE.toLocaleString('en-US')} characters at most` : `${note.length} of ${MAX_NOTE.toLocaleString('en-US')} characters`}
                  </p>
                  <button className="btn btn-accent" type="submit" disabled={!note.trim() || note.length > MAX_NOTE}>
                    Send it back
                  </button>
                </form>
              )}
            </div>
          )}
        </div>
      )}

      {data.waiting_for_next_dispatch && (
        <div className="notice">Sent back to the agent with a note. It is waiting for the next dispatch.</div>
      )}

      {payment?.status === 'paid' && payment.approved_by && (
        <div className="notice">
          Paid {formatUsd(payment.amount)}, signed off by {payment.approved_by_name ?? payment.approved_by}
          {payment.approved_at && <> on {formatTime(payment.approved_at)}</>}.
        </div>
      )}

      {data.status === 'needs_handler' && (
        <div className="notice notice-handler">
          Left for a handler: the agent failed {data.attempts} times on this claim and did not decide it.
          {data.last_error && <span className="mono small"> Last error: {data.last_error}</span>}
        </div>
      )}

      <div className="case">
        <div className="case-main">
          <div className="card pad">
            <h3>The customer's story</h3>
            <p className="story">{claim.story}</p>
          </div>

          <div className="card pad">
            <h3>Items claimed</h3>
            <table>
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Category</th>
                  <th className="num">Amount</th>
                </tr>
              </thead>
              <tbody>
                {claim.items.map((item, i) => (
                  <tr key={i}>
                    <td>{item.description}</td>
                    <td className="muted">{humanize(item.category)}</td>
                    <td className="num mono">{formatUsd(item.amount)}</td>
                  </tr>
                ))}
                <tr className="total">
                  <td>Total claimed</td>
                  <td />
                  <td className="num mono">{formatUsd(claim.amount_claimed)}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div className="card pad">
            <h3>Documents</h3>
            {claim.documents.map((d, i) => (
              <details key={i}>
                <summary>
                  {d.name} <span className="muted small">{humanize(d.type)}</span>
                </summary>
                <p className="doc">{d.content}</p>
              </details>
            ))}
          </div>
        </div>

        <aside className="case-rail">
          <div className="card pad verdict">
            <h3>Decision</h3>
            {decision ? (
              <>
                <div className="verdict-top">
                  <span
                    className={`decision decision-${stampFor(data.status, decision.decision).key} stamp stamp-${stampFor(data.status, decision.decision).key}`}
                  >
                    {stampFor(data.status, decision.decision).label}
                  </span>
                  <span className="payout mono">{formatUsd(decision.payout)}</span>
                </div>
                <p>{decision.rationale}</p>
                {decision.citations.length > 0 && <p className="muted small">{decision.citations.join(' · ')}</p>}
                {decision.calculation && decision.calculation.lines.length > 0 && (
                  <table className="calc">
                    <tbody>
                      {decision.calculation.lines.map((s, i) => (
                        <tr key={i}>
                          <td>{s.step}</td>
                          <td className="num mono">{formatUsd(s.amount)}</td>
                        </tr>
                      ))}
                      <tr className="total">
                        <td>Payout</td>
                        <td className="num mono">{formatUsd(decision.calculation.payout)}</td>
                      </tr>
                    </tbody>
                  </table>
                )}
                <p className="muted small">
                  Decided by {decision.decided_by ?? decision.model}
                  {decision.decided_at && <> on {formatTime(decision.decided_at)}</>}
                </p>
                {decision.signed_off_by && (
                  <p className="muted small">
                    Signed off by {decision.signed_off_by}
                    {decision.signed_off_at && <> on {formatTime(decision.signed_off_at)}</>}
                  </p>
                )}
                {decision.manual_version && (
                  <p className="muted small">
                    Claims manual {decision.manual_version} · policy wording {decision.wording_version}
                  </p>
                )}
                {data.usage && data.usage.cost_usd !== null && (
                  <p className="muted small">Analysis cost {describeUsage(data.usage)}</p>
                )}
              </>
            ) : (
              <p className="muted">No decision yet.</p>
            )}
          </div>

          {data.sent_back && data.sent_back.length > 0 && (
            <div className="card pad">
              <h3>Sent back</h3>
              {data.sent_back.map((b, i) => (
                <div key={i} className="sent-back">
                  <p className="muted small">
                    Sent back by {b.sent_back_by_name}
                    {b.sent_back_at && <> on {formatTime(b.sent_back_at)}</>}
                  </p>
                  <p>{b.note}</p>
                  <p className="muted small">
                    The decision sent back: {b.decision} <span className="sent-back-payout">{formatUsd(b.payout)}</span>. {b.rationale}
                  </p>
                </div>
              ))}
            </div>
          )}

          {data.context_used && data.context_used.length > 0 && (
            <div className="card pad">
              <h3>Context used</h3>
              {data.context_used.map((file) => (
                <div key={file.file} className="context-file">
                  <p className="mono small">{file.file}</p>
                  <p className="muted small">
                    {file.system} · {file.owner} · reviewed {file.reviewed}
                  </p>
                  <ul className="read-list">
                    {file.lookups.map((l, i) => (
                      <li key={i}>{capitalize(describeLookup(l.tool, l.args))}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}

          <div className="card pad">
            <h3>Policy and customer</h3>
            <p>
              {humanize(data.policy.plan)} plan · {data.policy.policy_id}
              {data.policy.add_ons.length > 0 && ` · add-ons: ${data.policy.add_ons.map(humanize).join(', ')}`}
            </p>
            <p className="muted small">Travelers: {data.policy.travelers.join(', ')}</p>
            <p className="muted small">
              {data.customer.previous_claims.length
                ? `Earlier claims: ${data.customer.previous_claims.map((c) => `${c.claim_id} (${c.outcome})`).join(', ')}`
                : 'No earlier claims'}
            </p>
          </div>
        </aside>
      </div>

      <Trace claimId={id} status={data.status} />
    </section>
  )
}
