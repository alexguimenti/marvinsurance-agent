import { useCallback, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, listEvaluations, startEvaluation, type EvalSet, type EvaluationSummary } from '../api'
import {
  DEFAULT_WORKERS,
  MAX_WORKERS,
  MIN_WORKERS,
  RUN_LABEL,
  SET_LABEL,
  describeSet,
  formatCost,
  formatGrade,
  formatSeconds,
  formatShare,
  formatTime,
  formatUsd,
  runRefused,
} from '../format'
import { usePolling } from '../usePolling'
import PageHeader from '../components/PageHeader'
import { ErrorState } from '../components/States'

const POLL_MS = 3000

/** Every evaluation, newest first, with the measures from the team's labs, and a way to start one. */
export default function Scoreboard() {
  const [evaluations, setEvaluations] = useState<EvaluationSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [evalSet, setEvalSet] = useState<EvalSet>('evaluation')
  const [tiny, setTiny] = useState(false)
  const [workers, setWorkers] = useState(String(DEFAULT_WORKERS))
  const [starting, setStarting] = useState(false)

  const load = useCallback(async () => {
    try {
      setEvaluations(await listEvaluations())
      setError(null)
    } catch {
      setError('Could not load the scoreboard.')
    }
  }, [])
  usePolling(load, POLL_MS)

  const n = Number(workers)
  const validN = Number.isInteger(n) && n >= MIN_WORKERS && n <= MAX_WORKERS
  const running = (evaluations ?? []).some((e) => e.run.status === 'running')

  async function onStart() {
    setStarting(true)
    try {
      await startEvaluation(evalSet, tiny, n)
      await load()
    } catch (e) {
      setError(runRefused(e instanceof ApiError ? e.status : undefined, 'Could not start the evaluation.'))
    } finally {
      setStarting(false)
    }
  }

  return (
    <section>
      <PageHeader
        eyebrow="How the agent is doing"
        title="Scoreboard"
        lede="Each evaluation works copies of a labelled set and compares the agent with the handlers. Copies never show in the queue, and nobody signs them off."
        actions={<div className="run-controls">
          <label className="limit">
            <span>Set</span>
            <select value={evalSet} onChange={(e) => setEvalSet(e.target.value as EvalSet)}>
              {Object.entries(SET_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="limit">
            <input type="checkbox" checked={tiny} onChange={(e) => setTiny(e.target.checked)} />
            <span>Only the five tiny claims</span>
          </label>
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
          <button className="btn btn-primary" onClick={onStart} disabled={starting || running || !validN}>
            Start evaluation
          </button>
        </div>}
      />
      {error && <ErrorState>{error}</ErrorState>}
      {evaluations?.length === 0 && <p className="muted">No evaluations yet. Start one and its measures appear here.</p>}
      {evaluations && evaluations.length > 0 && (
        <div className="card">
          <table className="queue scoreboard">
            <thead>
              <tr>
                <th>Evaluation</th>
                <th>Status</th>
                <th className="num">Decision accuracy</th>
                <th className="num">Accuracy</th>
                <th className="num">Grade</th>
                <th className="num">Cost of mistakes</th>
                <th className="num">Straight-through</th>
                <th className="num">Cost per claim</th>
                <th className="num">Time per claim</th>
                <th className="num">Total time</th>
              </tr>
            </thead>
            <tbody>
              {evaluations.map(({ run, measures: m }) => (
                <tr key={run.run_id}>
                  <td>
                    <Link className="case-id" to={`/scoreboard/${run.run_id}`}>
                      Evaluation {run.run_id}
                    </Link>
                    <div className="small">{describeSet(run.eval_set, run.tiny)}</div>
                    <div className="muted small">
                      {run.model} · effort {run.reasoning_effort} · {run.workers} at once · {formatTime(run.started_at)}
                    </div>
                  </td>
                  <td>
                    <span className={`run-status run-status-${run.status}`}>{RUN_LABEL[run.status]}</span>
                  </td>
                  <td className="num mono">{formatShare(m.decision_accuracy)}</td>
                  <td className="num mono">{formatShare(m.accuracy)}</td>
                  <td className="num mono">{formatGrade(m.grade)}</td>
                  <td className="num mono">{formatUsd(m.mistake_cost)}</td>
                  <td className="num mono">{formatShare(m.straight_through)}</td>
                  <td className="num mono">{m.cost_per_claim === null ? '—' : formatCost(m.cost_per_claim)}</td>
                  <td className="num mono">{m.seconds_per_claim === null ? '—' : formatSeconds(m.seconds_per_claim)}</td>
                  <td className="num mono">{formatSeconds(m.total_seconds)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
