import type { DiffLine } from '../api'

interface Change {
  before: string | null
  after: string | null
}

const FOLD_OVER = 6 // a run of unchanged lines longer than this folds
const CONTEXT = 2 // and keeps this many at each end

type Row = { kind: DiffLine['kind'] | 'fold'; text: string }

function fold(lines: DiffLine[]): Row[] {
  const rows: Row[] = []
  let i = 0
  while (i < lines.length) {
    if (lines[i].kind !== 'same') {
      rows.push(lines[i++])
      continue
    }
    let end = i
    while (end < lines.length && lines[end].kind === 'same') end++
    const run = lines.slice(i, end)
    if (run.length > FOLD_OVER) {
      rows.push(...run.slice(0, CONTEXT), { kind: 'fold', text: `… ${run.length - 2 * CONTEXT} unchanged lines` }, ...run.slice(-CONTEXT))
    } else {
      rows.push(...run)
    }
    i = end
  }
  return rows
}

const MARK: Record<Row['kind'], string> = { same: ' ', add: '+', remove: '−', fold: '' }

/** What a version changed, line by line: additions and removals in color, long unchanged runs folded. */
export default function DiffView({ lines, model, reasoningEffort }: { lines: DiffLine[]; model?: Change; reasoningEffort?: Change }) {
  const changed = lines.some((l) => l.kind !== 'same')
  const settings = (label: string, change: Change | undefined) => {
    if (!change) return null
    const moved = change.before !== change.after
    return (
      <p className={`diff-setting ${moved ? 'diff-setting-changed' : 'muted'}`}>
        {label}: {moved && change.before ? `${change.before} → ${change.after}` : `${change.after} (unchanged)`}
      </p>
    )
  }
  return (
    <div className="diff">
      {settings('Model', model)}
      {settings('Reasoning effort', reasoningEffort)}
      {!changed && <p className="muted small">No lines changed.</p>}
      <div className="diff-lines" role="group" aria-label="Changed lines">
        {fold(lines).map((row, i) => (
          <div key={i} className={`diff-line diff-${row.kind}`}>
            <span className="diff-mark" aria-hidden="true">{MARK[row.kind]}</span>
            <span className="diff-text">{row.text}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
