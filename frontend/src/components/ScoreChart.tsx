export interface ChartPoint {
  version: number
  accuracy: number | null
  grade: number | null
  kept: boolean
}

const W = 460
const H = 190
const PAD = { left: 44, right: 16, top: 12, bottom: 30 }

interface PanelProps {
  title: string
  points: ChartPoint[]
  value: (p: ChartPoint) => number | null
  max: number
  ticks: { at: number; label: string }[]
}

function Panel({ title, points, value, max, ticks }: PanelProps) {
  const inner = { w: W - PAD.left - PAD.right, h: H - PAD.top - PAD.bottom }
  const x = (i: number) => PAD.left + (points.length === 1 ? inner.w / 2 : (i / (points.length - 1)) * inner.w)
  const y = (v: number) => PAD.top + inner.h - (v / max) * inner.h
  const drawn = points.map((p, i) => ({ p, i, v: value(p) })).filter((d): d is { p: ChartPoint; i: number; v: number } => d.v !== null)
  const path = drawn.map((d, k) => `${k === 0 ? 'M' : 'L'}${x(d.i).toFixed(1)},${y(d.v).toFixed(1)}`).join(' ')
  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title} className="chart-svg">
        {ticks.map((t) => (
          <g key={t.label}>
            <line className="chart-grid" x1={PAD.left} x2={W - PAD.right} y1={y(t.at)} y2={y(t.at)} />
            <text className="chart-tick" x={PAD.left - 8} y={y(t.at) + 4} textAnchor="end">{t.label}</text>
          </g>
        ))}
        {drawn.length > 1 && <path className="chart-line" d={path} fill="none" />}
        {drawn.map((d) => (
          <g key={d.p.version}>
            <circle
              className={`point ${d.p.kept ? 'point-kept' : 'point-rolled-back'}`}
              data-kept={String(d.p.kept)}
              cx={x(d.i)}
              cy={y(d.v)}
              r={6}
            />
            <text className="chart-tick" x={x(d.i)} y={H - 10} textAnchor="middle">{`v${d.p.version}`}</text>
          </g>
        ))}
      </svg>
    </figure>
  )
}

/** The held-out accuracy and grade of each version tried, in SVG: a version a round kept is filled, one it rolled back is hollow. */
export default function ScoreChart({ points }: { points: ChartPoint[] }) {
  if (points.length === 0) {
    return <p className="muted">No version has been tried yet. Once a round has evaluated one, its held-out scores are drawn here.</p>
  }
  return (
    <div className="charts">
      <Panel title="Held-out accuracy of each version tried" points={points} value={(p) => p.accuracy} max={1}
        ticks={[{ at: 0, label: '0%' }, { at: 0.5, label: '50%' }, { at: 1, label: '100%' }]} />
      <Panel title="Held-out grade of each version tried" points={points} value={(p) => p.grade} max={3}
        ticks={[{ at: 0, label: '0' }, { at: 1, label: '1' }, { at: 2, label: '2' }, { at: 3, label: '3' }]} />
      <p className="muted small chart-legend">
        <span className="dot dot-kept" aria-hidden="true" /> kept <span className="dot dot-rolled-back" aria-hidden="true" /> rolled back
      </p>
    </div>
  )
}
