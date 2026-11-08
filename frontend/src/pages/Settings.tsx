import { useCallback, useState, type ReactNode } from 'react'
import { ApiError, changeSetting, getSettings, type PlatformSettings, type SettingRow } from '../api'
import { useAuth } from '../auth/context'
import ConfirmDialog from '../components/ConfirmDialog'
import { formatTime, formatUsd } from '../format'
import { getThemeChoice, setThemeChoice, type ThemeChoice } from '../theme'
import { usePolling } from '../usePolling'

const MEASURES: [string, string][] = [
  ['accuracy', 'accuracy'],
  ['grade', 'grade'],
  ['cost_per_claim', 'cost per claim'],
  ['seconds_per_claim', 'time per claim'],
]

type Draft = Record<string, string>

interface Group {
  setting: string
  title: string
  help: string
  fields: { key: string | null; label: string; step: number; min: number; max?: number }[]
  describe: (value: unknown) => string
}

const money = (v: unknown) => formatUsd(Number(v))

const GROUPS: Group[] = [
  {
    setting: 'approval_limit', title: 'Approval limit',
    help: 'A payout above this waits for a handler who can sign it off. A new limit applies to decisions recorded after the change.',
    fields: [{ key: null, label: 'Approval limit (USD)', step: 50, min: 0 }], describe: money,
  },
  {
    setting: 'objectives', title: 'Business objectives',
    help: 'Floors on the held-out set that a new version must meet to be kept.',
    fields: [
      { key: 'accuracy', label: 'Accuracy floor', step: 0.05, min: 0, max: 1 },
      { key: 'grade', label: 'Grade floor', step: 0.1, min: 0, max: 3 },
    ],
    describe: (v) => { const o = v as Record<string, number>; return `accuracy ${Math.round(o.accuracy * 100)}% and grade ${o.grade}` },
  },
  {
    setting: 'noise', title: 'Noise',
    help: 'How far a measure must improve before it counts. Accuracy and the grade move by points; cost and time by a fraction of the old value (0.15 is 15%).',
    fields: MEASURES.map(([key, label]) => ({ key, label: `Noise: ${label}`, step: 0.05, min: 0 })),
    describe: (v) => Object.entries(v as Record<string, number>).map(([k, n]) => `${k.replace(/_/g, ' ')} ${n}`).join(', '),
  },
  {
    setting: 'tolerance', title: 'Tolerance',
    help: 'How far a measure may get worse in a version that improves another. Same units as the noise.',
    fields: MEASURES.map(([key, label]) => ({ key, label: `Tolerance: ${label}`, step: 0.05, min: 0 })),
    describe: (v) => Object.entries(v as Record<string, number>).map(([k, n]) => `${k.replace(/_/g, ' ')} ${n}`).join(', '),
  },
  {
    setting: 'round_spending_limit', title: 'Spending limit for each round',
    help: 'Covers the improver, all of the round’s evaluations and the judge. A round works to the limit it started with.',
    fields: [{ key: null, label: 'Spending limit for each round (USD)', step: 0.05, min: 0 }], describe: money,
  },
  {
    setting: 'agents_at_once', title: 'Claim agents at once',
    help: 'How many claim agents work at the same time in the improver’s evaluations, from 1 to 25.',
    fields: [{ key: null, label: 'Claim agents at once', step: 1, min: 1, max: 25 }], describe: (v) => String(v),
  },
]

function toDraft(group: Group, value: unknown): Draft {
  if (group.fields[0].key === null) return { value: String(value) }
  return Object.fromEntries(group.fields.map((f) => [f.key!, String((value as Record<string, number>)[f.key!])]))
}

function fromDraft(group: Group, draft: Draft): unknown {
  if (group.fields[0].key === null) return Number(draft.value)
  return Object.fromEntries(group.fields.map((f) => [f.key!, Number(draft[f.key!])]))
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

export default function Settings() {
  const { handler } = useAuth()
  const [data, setData] = useState<PlatformSettings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [asking, setAsking] = useState<Group | null>(null)
  const [busy, setBusy] = useState(false)
  const [theme, setTheme] = useState<ThemeChoice>(getThemeChoice())
  const canChange = handler.can_approve_over_limit

  const load = useCallback(async () => {
    try {
      setData(await getSettings())
    } catch {
      setError('Could not load the settings. Reload the page to try again.')
    }
  }, [])
  usePolling(load, null)

  const byName = (name: string): SettingRow | undefined => data?.settings.find((s) => s.name === name)
  const draftOf = (group: Group): Draft => drafts[group.setting] ?? toDraft(group, byName(group.setting)?.value)
  const proposed = (group: Group) => fromDraft(group, draftOf(group))
  const valid = (group: Group) =>
    group.fields.every((f) => {
      const text = draftOf(group)[f.key ?? 'value']
      const n = Number(text)
      return text.trim() !== '' && Number.isFinite(n) && n >= f.min && (f.max === undefined || n <= f.max)
    })
  const changed = (group: Group) => !same(proposed(group), byName(group.setting)?.value)

  async function confirm() {
    if (!asking) return
    setBusy(true)
    try {
      await changeSetting(asking.setting, proposed(asking))
      setDrafts((d) => Object.fromEntries(Object.entries(d).filter(([k]) => k !== asking.setting)))
      setError(null)
      setAsking(null)
      await load()
    } catch (e) {
      setAsking(null)
      setError(
        e instanceof ApiError && e.status === 403
          ? 'You are not allowed to change settings.'
          : e instanceof ApiError
            ? `The setting was not changed: ${e.message}`
            : 'Could not change the setting.',
      )
    } finally {
      setBusy(false)
    }
  }

  function chooseTheme(choice: ThemeChoice) {
    setThemeChoice(choice)
    setTheme(choice)
  }

  return (
    <section>
      <div className="page-head">
        <div>
          <p className="eyebrow">Platform</p>
          <h1>Settings</h1>
          <p className="lede">The limits and measures the whole platform works to. Every change records who made it and when.</p>
        </div>
      </div>
      {!canChange && (
        <p className="notice" role="note">
          Only a handler who can sign off payouts over the limit can change settings. You can look at them, and change how this browser looks.
        </p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      {!data && !error && <p className="muted">Loading the settings</p>}
      {data &&
        GROUPS.map((group) => {
          const row = byName(group.setting)
          const draft = draftOf(group)
          return (
            <form key={group.setting} className="card pad setting" onSubmit={(e) => { e.preventDefault(); setAsking(group) }}>
              <div className="setting-head">
                <h2>{group.title}</h2>
                <p className="muted small">{group.help}</p>
              </div>
              <div className="setting-fields">
                {group.fields.map((f) => (
                  <label key={f.label} className="field">
                    <span>{f.label}</span>
                    <input
                      type="number"
                      className="num mono"
                      aria-label={f.label}
                      step={f.step}
                      min={f.min}
                      max={f.max}
                      disabled={!canChange}
                      value={draft[f.key ?? 'value']}
                      onChange={(e) => setDrafts((d) => ({ ...d, [group.setting]: { ...draft, [f.key ?? 'value']: e.target.value } }))}
                    />
                  </label>
                ))}
              </div>
              <div className="setting-foot">
                <p className="muted small">
                  {row?.changed_by_name ? `Last changed by ${row.changed_by_name} on ${formatTime(row.changed_at)}` : 'Starting value, not changed since'}
                </p>
                {canChange && (
                  <button className="btn btn-primary" type="submit" disabled={!changed(group) || !valid(group)} aria-label={`Save ${group.title.toLowerCase()}`}>
                    Save
                  </button>
                )}
              </div>
            </form>
          )
        })}
      {data && (
        <div className="card pad">
          <div className="setting-head">
            <h2>Models the claim agent may use</h2>
            <p className="muted small">Set in a file, not on this page. Prices are in dollars per million tokens.</p>
          </div>
          <table aria-label="Models allowed">
            <thead>
              <tr>
                <th>Model</th>
                <th>Reasoning efforts</th>
                <th className="num">Input</th>
                <th className="num">Output</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.models_allowed).map(([model, m]) => (
                <tr key={model}>
                  <td className="mono">{model}</td>
                  <td>{m.reasoning_efforts.join(', ')}</td>
                  <td className="num mono">{formatUsd(m.price_in)}</td>
                  <td className="num mono">{formatUsd(m.price_out)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <fieldset className="card pad appearance">
        <legend>Appearance</legend>
        <p className="muted small">Kept in this browser only.</p>
        <div className="row">
          {(['light', 'dark', 'system'] as ThemeChoice[]).map((choice) => (
            <label key={choice} className="radio">
              <input type="radio" name="theme" checked={theme === choice} onChange={() => chooseTheme(choice)} />
              <span>{choice === 'system' ? 'Same as this computer' : choice === 'light' ? 'Light' : 'Dark'}</span>
            </label>
          ))}
        </div>
      </fieldset>
      {asking && (
        <ConfirmDialog title={`Change ${asking.title.toLowerCase()}?`} confirmLabel="Change the setting" busy={busy} onConfirm={confirm} onCancel={() => setAsking(null)}>
          <Summary group={asking} from={byName(asking.setting)?.value} to={proposed(asking)} />
        </ConfirmDialog>
      )}
    </section>
  )
}

function Summary({ group, from, to }: { group: Group; from: unknown; to: unknown }): ReactNode {
  return (
    <p>
      From {group.describe(from)} to {group.describe(to)}. It applies from now on, and the change is recorded with your name.
    </p>
  )
}
