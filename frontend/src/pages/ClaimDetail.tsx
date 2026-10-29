import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getClaim, runClaim, type ClaimDetailData } from '../api'
import StatusBadge from '../components/StatusBadge'
import { DECISION_LABEL, describeUsage, formatUsd, humanize } from '../format'
import Trace from './Trace'

const POLL_MS = 2000

export default function ClaimDetail() {
  const { id = '' } = useParams()
  const [data, setData] = useState<ClaimDetailData | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await getClaim(id))
      setError(null)
    } catch {
      setError('Could not load this claim.')
    }
  }, [id])

  useEffect(() => {
    void load()
    const timer = setInterval(() => void load(), POLL_MS)
    return () => clearInterval(timer)
  }, [load])

  async function onRun() {
    try {
      await runClaim(id)
      await load()
    } catch {
      setError('Could not start this claim.')
    }
  }

  if (error && !data) return <p className="error">{error}</p>
  if (!data) return <p className="muted">Loading</p>

  const { claim, decision, payment } = data
  return (
    <section>
      <p className="crumb">
        <Link to="/">← Claims queue</Link>
      </p>
      <div className="page-head">
        <div>
          <p className="eyebrow">Case file</p>
          <h1>{claim.claim_id}</h1>
          <p className="lede">
            {humanize(claim.claim_type)} · {data.customer.name} · {claim.incident_location} · incident{' '}
            {claim.incident_date}
          </p>
        </div>
        <div className="row">
          <StatusBadge status={data.status} />
          {data.status === 'waiting' && (
            <button className="btn btn-primary" onClick={onRun}>
              Run this claim
            </button>
          )}
        </div>
      </div>
      {error && <p className="error">{error}</p>}

      {data.status === 'awaiting_approval' && payment && (
        <div className="notice">
          Held for approval: the payout of {formatUsd(payment.amount)} is above the $1,000 limit and waits for a handler.{' '}
          <Link to="/approvals">Go to approvals</Link>
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
                  <span className={`decision decision-${decision.decision} stamp stamp-${decision.decision}`}>
                    {DECISION_LABEL[decision.decision]}
                  </span>
                  <span className="payout mono">{formatUsd(decision.payout)}</span>
                </div>
                <p>{decision.rationale}</p>
                {decision.citations.length > 0 && <p className="muted small">{decision.citations.join(' · ')}</p>}
                {decision.calculation.length > 0 && (
                  <table className="calc">
                    <tbody>
                      {decision.calculation.map((s, i) => (
                        <tr key={i}>
                          <td>{s.step}</td>
                          <td className="num mono">{formatUsd(s.amount)}</td>
                        </tr>
                      ))}
                      <tr className="total">
                        <td>Payout</td>
                        <td className="num mono">{formatUsd(decision.payout)}</td>
                      </tr>
                    </tbody>
                  </table>
                )}
                <p className="muted small">Decided by {decision.model}</p>
                {data.usage && data.usage.cost_usd !== null && (
                  <p className="muted small">Analysis cost {describeUsage(data.usage)}</p>
                )}
              </>
            ) : (
              <p className="muted">No decision yet.</p>
            )}
          </div>

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
