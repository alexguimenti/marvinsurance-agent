import { useCallback, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getEvaluation, type EvaluationReport } from '../api'
import { DECISION_LABEL, RUN_LABEL, describeSet, formatCost, formatGrade, formatSeconds, formatShare, formatUsd } from '../format'
import { usePolling } from '../usePolling'
import PageHeader from '../components/PageHeader'
import { ErrorState, Loading } from '../components/States'

const POLL_MS = 3000

/** A check the judge answered with a probability, shown as yes or no. */
function Check({ label, probability }: { label: string; probability: number | null }) {
  const yes = (probability ?? 0) >= 0.5
  return (
    <span aria-label={`${label}: ${yes ? 'yes' : 'no'}`} className={yes ? 'check-yes' : 'check-no'}>
      {label.replace('Same ', '')} {yes ? '✓' : '✗'}
    </span>
  )
}

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

  if (error && !data) return <ErrorState>{error}</ErrorState>
  if (!data) return <Loading what="the page" />
  const { run, measures: m, claims } = data

  return (
    <section>
      <p className="crumb">
        <Link to="/scoreboard">← Scoreboard</Link>
      </p>
      <PageHeader
        eyebrow={describeSet(run.eval_set, run.tiny)}
        title={<>Evaluation {run.run_id}</>}
        lede={<>{RUN_LABEL[run.status]} · {run.model} · effort {run.reasoning_effort} · {run.workers} claim agents at once</>}
      />
      {run.error && <p className="error small">Stopped: {run.error}</p>}
      <div className="tiles">
        {[
          ['Decision accuracy', formatShare(m.decision_accuracy)],
          ['Accuracy', formatShare(m.accuracy)],
          ['Grade', formatGrade(m.grade)],
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
              <th>Explanation</th>
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
                <td>
                  {c.score === null || c.letter === null ? (
                    <span className="muted">Not graded</span>
                  ) : (
                    <div className="grade">
                      <span className={`pill pill-grade-${c.letter}`}>{c.letter}</span> <span className="num mono">{c.score.toFixed(2)}</span>
                      <div className="small muted">
                        <Check label="Same grounds" probability={c.same_grounds} /> <Check label="Same section" probability={c.same_section} />
                      </div>
                    </div>
                  )}
                </td>
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
