import { useCallback, useState } from 'react'
import {
  ApiError,
  decideProposal,
  getConfiguration,
  getSettings,
  getVersionDiff,
  listRounds,
  startRound,
  stopRound,
  type ConfigurationData,
  type RoundView,
  type VersionDiff,
} from '../api'
import { useAuth } from '../auth/context'
import ConfirmDialog from '../components/ConfirmDialog'
import DiffView from '../components/DiffView'
import ScoreChart, { type ChartPoint } from '../components/ScoreChart'
import { formatShare, formatTime, formatUsd } from '../format'
import { usePolling } from '../usePolling'
import PageHeader from '../components/PageHeader'
import StatusPill from '../components/StatusPill'
import { Empty, ErrorState, Loading } from '../components/States'

const STEPS: [string, string][] = [
  ['first_evaluations', 'First evaluations'],
  ['improving', 'The improver at work'],
  ['awaiting_change', 'A change waiting for approval'],
  ['evaluating', 'Evaluating the new version'],
  ['verdict', 'Verdict'],
  ['awaiting_recipe', 'A recipe waiting for approval'],
  ['finished', 'Finished'],
]
const ORDER = STEPS.map(([key]) => key)
const POLL_MS = 2000
const NOT_ALLOWED =
  "Only a handler who can sign off payouts over the limit can start or stop a round or approve what the improver proposes. You can read everything here, and the settings it works to are on the Settings page."

const grade = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v.toFixed(2))
const before_after = (a: number | null | undefined, b: number | null | undefined, f: (v: number | null | undefined) => string) =>
  a === undefined && b === undefined ? '—' : `${f(a)} → ${f(b)}`

type Dialog =
  | { kind: 'start' }
  | { kind: 'stop'; round: RoundView }
  | { kind: 'approve' | 'reject'; round: RoundView }

function Steps({ round }: { round: RoundView }) {
  const at = ORDER.indexOf(round.stage)
  return (
    <ol className="steps" aria-label="Steps of a round">
      {STEPS.map(([key, label], i) => {
        const state = round.stage === 'stopped' ? 'todo' : i < at ? 'done' : i === at ? 'current' : 'todo'
        return (
          <li key={key} className={`step step-${state}`} aria-current={state === 'current' ? 'step' : undefined}>
            {label}
            {state === 'current' && round.progress && <span className="step-progress">{round.progress}</span>}
          </li>
        )
      })}
    </ol>
  )
}

function decisions(round: RoundView) {
  return round.proposals
    .filter((p) => p.status !== 'waiting')
    .map((p) => {
      const what = p.kind === 'change' ? 'the change' : 'the recipe'
      const who = p.decided_by_name ?? p.decided_by
      return `${who} ${p.status === 'approved' ? 'approved' : 'rejected'} ${what}${p.decision_reason ? `: ${p.decision_reason}` : ''}`
    })
}

export default function Improvement() {
  const { handler } = useAuth()
  const canAct = handler.can_approve_over_limit
  const [rounds, setRounds] = useState<RoundView[] | null>(null)
  const [config, setConfig] = useState<ConfigurationData | null>(null)
  const [limit, setLimit] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const [why, setWhy] = useState('')
  const [busy, setBusy] = useState(false)
  const [diff, setDiff] = useState<VersionDiff | null>(null)

  const load = useCallback(async () => {
    try {
      setRounds(await listRounds())
      setError(null)
    } catch {
      setError('Could not load the rounds. Reload the page to try again.')
    }
  }, [])
  usePolling(load, POLL_MS)

  const loadOnce = useCallback(async () => {
    try {
      setConfig(await getConfiguration())
      const settings = await getSettings()
      setLimit(Number(settings.settings.find((s) => s.name === 'round_spending_limit')?.value ?? 0))
    } catch {
      /* the page still works without the recipes and the limit */
    }
  }, [])
  usePolling(loadOnce, null)

  const open = rounds?.find((r) => r.open) ?? null
  const points: ChartPoint[] = (rounds ?? [])
    .filter((r) => r.verdict && r.version_tried !== null && r.after)
    .map((r) => ({ version: r.version_tried!, accuracy: r.after!.accuracy ?? null, grade: r.after!.grade ?? null, kept: r.verdict === 'kept' }))
    .sort((a, b) => a.version - b.version)

  async function act(run: () => Promise<unknown>) {
    setBusy(true)
    setActionError(null)
    try {
      await run()
      setDialog(null)
      setWhy('')
      await load()
    } catch (e) {
      setDialog(null)
      setActionError(
        e instanceof ApiError && e.status === 403 ? 'You are not allowed to do that.'
          : e instanceof ApiError && e.status === 503 ? 'The agent is not running. Start it with npm run dev.'
          : e instanceof ApiError ? e.message
          : 'That did not work. Try again.',
      )
    } finally {
      setBusy(false)
    }
  }

  async function showRecipe(version: number) {
    try {
      setDiff(await getVersionDiff('recipe', version))
    } catch {
      setActionError('Could not load those changes. Try again.')
    }
  }

  const waiting = open?.waiting ?? null

  return (
    <section>
      <PageHeader
        eyebrow="Claim agent"
        title="Improvement"
        lede="A round has the improver read the results, propose a change and have the new version evaluated. You approve every proposal, and code keeps the version or rolls it back on the held-out claims."
        actions={canAct && (
          <>
            <button className="btn btn-primary" disabled={open !== null || busy} onClick={() => setDialog({ kind: 'start' })}>
              Start round
            </button>
            {open && <p className="muted small">Round {open.round_id} is open: stop it, or wait for it to end, before you start another.</p>}
          </>
        )}
      />
      {!canAct && <p className="notice" role="note">{NOT_ALLOWED}</p>}
      {error && <ErrorState>{error}</ErrorState>}
      {actionError && <ErrorState>{actionError}</ErrorState>}
      {!rounds && !error && <Loading what="the rounds" />}

      {open && (
        <section className="card pad" aria-label="Open round">
          <div className="setting-head">
            <h2>Round {open.round_id}</h2>
            <p className="muted small">
              Started by {open.started_by_name ?? open.started_by} · {formatUsd(open.spent_usd)} of {formatUsd(open.spending_limit)} spent
            </p>
          </div>
          <Steps round={open} />
          {canAct && (
            <div className="row">
              <button className="btn btn-secondary" onClick={() => setDialog({ kind: 'stop', round: open })}>Stop round</button>
            </div>
          )}
        </section>
      )}

      {open && waiting && (
        <section className="card pad" aria-label="Waiting for your decision">
          <h2>{waiting.kind === 'change' ? 'A change to the claim agent is proposed' : 'A new recipe is proposed'}</h2>
          <p className="small">{waiting.reason}</p>
          <DiffView lines={waiting.lines} model={waiting.model} reasoningEffort={waiting.reasoning_effort} />
          {canAct && (
            <div className="row">
              <button className="btn btn-primary" onClick={() => setDialog({ kind: 'approve', round: open })}>Approve</button>
              <button className="btn btn-secondary" onClick={() => setDialog({ kind: 'reject', round: open })}>Reject</button>
            </div>
          )}
        </section>
      )}

      <div className="card pad">
        <h2>Held-out scores of each version tried</h2>
        <ScoreChart points={points} />
      </div>

      <div className="card pad">
        <h2>Rounds</h2>
        {rounds && rounds.length === 0 && <Empty>No rounds yet. Start one and its results appear here.</Empty>}
        {rounds && rounds.length > 0 && (
          <table aria-label="Rounds">
            <thead>
              <tr>
                <th>Round</th>
                <th>Recipe</th>
                <th>Version tried</th>
                <th className="num">Held-out accuracy</th>
                <th className="num">Held-out grade</th>
                <th>Outcome</th>
                <th>Decisions</th>
                <th className="num">Cost</th>
              </tr>
            </thead>
            <tbody>
              {rounds.map((r) => (
                <tr key={r.round_id}>
                  <td>
                    <strong>Round {r.round_id}</strong>
                    <div className="muted small">{formatTime(r.started_at)}</div>
                  </td>
                  <td className="mono">v{r.recipe_version}</td>
                  <td className="mono">{r.version_tried === null ? '—' : `v${r.version_tried}`}</td>
                  <td className="num mono">{r.after ? before_after(r.before?.accuracy, r.after.accuracy, (v) => formatShare(v ?? null)) : '—'}</td>
                  <td className="num mono">{r.after ? before_after(r.before?.grade, r.after.grade, grade) : '—'}</td>
                  <td>
                    <span className={`pill pill-${r.stage === 'stopped' ? 'stopped' : (r.verdict ?? r.stage)}`}>
                      {r.stage === 'stopped' ? 'Stopped' : r.verdict === 'kept' ? 'Kept' : r.verdict === 'rolled_back' ? 'Rolled back' : r.open ? 'Open' : 'Finished'}
                    </span>
                    {r.why && <div className="small">{r.why}</div>}
                    {r.error && <div className="small error-text">{r.error}</div>}
                  </td>
                  <td className="small">
                    {decisions(r).map((d) => <div key={d}>{d}</div>)}
                    {r.stopped_by_name && <div>{r.stopped_by_name} stopped the round</div>}
                  </td>
                  <td className="num mono">{formatUsd(r.spent_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {config && (
        <div className="card pad">
          <h2>Recipe versions</h2>
          <p className="muted small">The recipe the improver follows. The last step of each round is to improve it, and a handler approves the new one.</p>
          <table aria-label="Recipe versions">
            <thead>
              <tr>
                <th>Version</th>
                <th>What changed</th>
                <th>Approved by</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {config.recipes.map((r) => (
                <tr key={r.version}>
                  <td className="mono">{r.version}{r.version === config.current_recipe && <> <StatusPill tone="success">Current</StatusPill></>}</td>
                  <td>{r.summary}</td>
                  <td>{r.approved_by_name ?? (r.approved_by === 'seed' ? 'Starting point' : r.approved_by)}</td>
                  <td className="num">
                    <button className="btn btn-secondary" aria-label={`Show changes in recipe ${r.version}`} onClick={() => void showRecipe(r.version)}>Show changes</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {diff && (
            <div>
              <p className="muted small">
                Recipe {diff.version}: {diff.against === null ? 'the first version.' : `compared with version ${diff.against}.`} {diff.summary}
              </p>
              <DiffView lines={diff.lines} />
            </div>
          )}
        </div>
      )}

      {dialog?.kind === 'start' && (
        <ConfirmDialog title="Start a round?" confirmLabel="Start the round" busy={busy} onConfirm={() => void act(() => startRound())} onCancel={() => setDialog(null)}>
          <p>
            The round runs the evaluations the current version still needs, then the improver, which proposes changes for you to approve. It spends up to {formatUsd(limit ?? 0)}, its spending limit, on the improver, the evaluations and the judge.
          </p>
        </ConfirmDialog>
      )}
      {dialog?.kind === 'stop' && (
        <ConfirmDialog title="Stop this round?" confirmLabel="Stop the round" danger busy={busy} onConfirm={() => void act(() => stopRound(dialog.round.round_id))} onCancel={() => setDialog(null)}>
          <p>Round {dialog.round.round_id} ends now. A version it has already kept stays the current one; anything it was still working on is dropped.</p>
        </ConfirmDialog>
      )}
      {dialog?.kind === 'approve' && waiting && (
        <ConfirmDialog
          title={waiting.kind === 'change' ? 'Approve this change?' : 'Approve this recipe?'}
          confirmLabel={waiting.kind === 'change' ? 'Approve the change' : 'Approve the recipe'}
          busy={busy}
          onConfirm={() => void act(() => decideProposal(dialog.round.round_id, waiting.id, true, ''))}
          onCancel={() => setDialog(null)}
        >
          <p>
            {waiting.kind === 'change'
              ? 'It becomes a new version, and the improver has it evaluated. The current version stays as it is unless the evaluations keep the new one.'
              : 'It becomes the recipe the improver follows from the next round on.'}{' '}
            Your name is recorded with the approval.
          </p>
        </ConfirmDialog>
      )}
      {dialog?.kind === 'reject' && waiting && (
        <ConfirmDialog
          title={waiting.kind === 'change' ? 'Reject this change?' : 'Reject this recipe?'}
          confirmLabel={waiting.kind === 'change' ? 'Reject the change' : 'Reject the recipe'}
          danger
          busy={busy || why.trim() === ''}
          onConfirm={() => void act(() => decideProposal(dialog.round.round_id, waiting.id, false, why.trim()))}
          onCancel={() => { setDialog(null); setWhy('') }}
        >
          <label htmlFor="why">Why are you rejecting it?</label>
          <textarea id="why" rows={3} value={why} onChange={(e) => setWhy(e.target.value)} />
          <p className="muted small">The improver reads your reason and may propose again.</p>
        </ConfirmDialog>
      )}
    </section>
  )
}
