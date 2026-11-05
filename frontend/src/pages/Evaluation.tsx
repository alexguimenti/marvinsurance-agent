import { useCallback, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getEvaluation, type EvaluationReport } from '../api'
import { DECISION_LABEL, RUN_LABEL, describeSet, formatCost, formatSeconds, formatShare, formatUsd } from '../format'
import { usePolling } from '../usePolling'

const POLL_MS = 3000

/** One evaluation: its measures, and each claim with the handler's answer beside the agent's. */
export default function Evaluation() {
  const { id = '' } = useParams()
  const [data, setData] = useState<EvaluationReport | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await getEvaluation(id))
      setError(null)
    } catch {
      setError('Could not load this evaluation.')
    }
  }, [id])
  usePolling(load, POLL_MS)

  if (error && !data) return <p className="error">{error}</p>
  if (!data) return <p className="muted">Loading</p>
  const { run, measures: m, claims } = data

  return (
    <section>
      <p className="crumb">
        <Link to="/scoreboard">← Scoreboard</Link>
      </p>
      <div className="page-head">
        <div>
          <p className="eyebrow">{describeSet(run.eval_set, run.tiny)}</p>
          <h1>Evaluation {run.run_id}</h1>
          <p className="lede">
            {RUN_LABEL[run.status]} · {run.model} · effort {run.reasoning_effort} · {run.workers} claim agents at once
          </p>
        </div>
      </div>
      <div className="tiles">
        {[
          ['Decision accuracy', formatShare(m.decision_accuracy)],
          ['Accuracy', formatShare(m.accuracy)],
          ['Cost of mistakes', formatUsd(m.mistake_cost)],
          ['Straight-through', formatShare(m.straight_through)],
          ['Cost per claim', m.cost_per_claim === null ? '—' : formatCost(m.cost_per_claim)],
          ['Time per claim', m.seconds_per_claim === null ? '—' : formatSeconds(m.seconds_per_claim)],
          ['Total time', formatSeconds(m.total_seconds)],
        ].map(([label, value]) => (
          <div key={label} className="tile" role="group" aria-label={label}>
            <span className="tile-n">{value}</span>
            <span className="tile-l">{label}</span>
          </div>
        ))}
      </div>
      <div className="card">
        <table className="queue evaluation">
          <thead>
            <tr>
              <th>Claim</th>
              <th>Agent</th>
              <th className="num">Agent's payout</th>
              <th>Handler</th>
              <th className="num">Handler's payout</th>
              <th className="num">Cost of the mistake</th>
              <th className="num">Model cost</th>
              <th className="num">Time</th>
            </tr>
          </thead>
          <tbody>
            {claims.map((c) => (
              <tr key={c.claim_id} className={c.accurate ? undefined : 'miss'}>
                <td>
                  <Link className="case-id" to={`/claims/${c.claim_id}`}>
                    {c.copy_of}
                  </Link>
                  {c.agent && <div className="muted small">{c.agent.rationale}</div>}
                </td>
                <td>{c.agent ? DECISION_LABEL[c.agent.decision] : <span className="muted">No decision</span>}</td>
                <td className="num mono">{c.agent ? formatUsd(c.agent.payout) : '—'}</td>
                <td>{DECISION_LABEL[c.handler.decision]}</td>
                <td className="num mono">{formatUsd(c.handler.payout)}</td>
                <td className="num mono">{c.mistake_cost ? formatUsd(c.mistake_cost) : '—'}</td>
                <td className="num mono">{c.cost_usd === null ? '—' : formatCost(c.cost_usd)}</td>
                <td className="num mono">{formatSeconds(c.seconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
