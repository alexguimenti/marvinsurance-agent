import { useCallback, useState } from 'react'
import { getConfiguration, getVersionDiff, type ConfigurationData, type VersionDiff, type VersionRow } from '../api'
import DiffView from '../components/DiffView'
import { formatTime } from '../format'
import { usePolling } from '../usePolling'

const WHO: Record<string, string> = { seed: 'Starting point', improver: 'The improver' }

const who = (row: VersionRow, by: 'proposed_by' | 'approved_by') =>
  by === 'approved_by' && row.approved_by_name ? row.approved_by_name : (WHO[row[by]] ?? row[by])

const percent = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${Math.round(v * 100)}%`)
const grade = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v.toFixed(2))

/** The claim agent's configuration in versions, and the improver's recipe in versions, with what each changed. */
export default function Configuration() {
  const [data, setData] = useState<ConfigurationData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [diff, setDiff] = useState<VersionDiff | null>(null)
  const [diffError, setDiffError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await getConfiguration())
      setError(null)
    } catch {
      setError('Could not load the configuration. Reload the page to try again.')
    }
  }, [])
  usePolling(load, null)

  async function show(kind: 'config' | 'recipe', version: number) {
    setDiffError(null)
    try {
      setDiff(await getVersionDiff(kind, version))
    } catch {
      setDiffError('Could not load those changes. Try again.')
    }
  }

  return (
    <section>
      <div className="page-head">
        <div>
          <p className="eyebrow">Claim agent</p>
          <h1>Configuration</h1>
          <p className="lede">
            The instructions, model and reasoning effort the claim agent works from, kept in versions, and the recipe the improver follows. A new version becomes the current one only when a round keeps it.
          </p>
        </div>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {!data && !error && <p className="muted">Loading the configuration</p>}
      {data && (
        <>
          <section className="card pad" aria-label="Current version">
            <div className="setting-head">
              <h2>Version {data.current.version}</h2>
              <p className="muted small">
                {data.current.model} · reasoning effort {data.current.reasoning_effort} · every dispatch and evaluation uses it
              </p>
            </div>
            <pre className="instructions-text">{data.current.instructions}</pre>
          </section>

          <div className="card pad">
            <h2>Versions</h2>
            <table aria-label="Versions">
              <thead>
                <tr>
                  <th>Version</th>
                  <th>What changed</th>
                  <th>Why</th>
                  <th>Approved by</th>
                  <th className="num">Held-out accuracy</th>
                  <th className="num">Held-out grade</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.versions.map((v) => {
                  const scores = v.scores?.['held-out']
                  return (
                    <tr key={v.version}>
                      <td>
                        <span className="mono">{v.version}</span>{' '}
                        {v.version === data.current.version && <span className="pill pill-success">Current</span>}
                      </td>
                      <td>
                        {v.summary}
                        <div className="muted small">{v.model} at {v.reasoning_effort}</div>
                      </td>
                      <td className="small">{v.reason}</td>
                      <td>
                        {who(v, 'approved_by')}
                        <div className="muted small">{v.approved_at ? formatTime(v.approved_at) : ''}</div>
                      </td>
                      <td className="num mono">{percent(scores?.accuracy)}</td>
                      <td className="num mono">{grade(scores?.grade)}</td>
                      <td className="num">
                        <button className="btn btn-secondary" onClick={() => void show('config', v.version)} aria-label={`Show changes in version ${v.version}`}>
                          Show changes
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div className="card pad">
            <h2>Recipe versions</h2>
            <p className="muted small">The recipe the improver follows. Its last step is to improve the recipe.</p>
            <table aria-label="Recipe versions">
              <thead>
                <tr>
                  <th>Version</th>
                  <th>What changed</th>
                  <th>Why</th>
                  <th>Approved by</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.recipes.map((r) => (
                  <tr key={r.version}>
                    <td>
                      <span className="mono">{r.version}</span>{' '}
                      {r.version === data.current_recipe && <span className="pill pill-success">Current</span>}
                    </td>
                    <td>{r.summary}</td>
                    <td className="small">{r.reason}</td>
                    <td>
                      {who(r, 'approved_by')}
                      <div className="muted small">{r.approved_at ? formatTime(r.approved_at) : ''}</div>
                    </td>
                    <td className="num">
                      <button className="btn btn-secondary" onClick={() => void show('recipe', r.version)} aria-label={`Show changes in recipe ${r.version}`}>
                        Show changes
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {diffError && <p className="error" role="alert">{diffError}</p>}
          {diff && (
            <section className="card pad" aria-label="Changes">
              <h2>
                {diff.kind === 'config' ? 'Version' : 'Recipe'} {diff.version}
              </h2>
              <p className="muted small">
                {diff.against === null ? 'The first version: everything is new.' : `Compared with version ${diff.against}.`} {diff.summary}
              </p>
              <DiffView lines={diff.lines} model={diff.model} reasoningEffort={diff.reasoning_effort} />
            </section>
          )}
        </>
      )}
    </section>
  )
}
