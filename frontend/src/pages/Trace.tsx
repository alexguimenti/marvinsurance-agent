import { useEffect, useState } from 'react'
import { getTrace, type TraceEvent } from '../api'
import { describeUsage, formatUsd, humanize } from '../format'

const POLL_MS = 1000

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

/** The claim comes wrapped in <claim_data> tags; everything else is plain JSON. */
function parseJson(raw: string): unknown | undefined {
  try {
    return JSON.parse(raw.replace(/<\/?claim_data>/g, '').trim())
  } catch {
    return undefined
  }
}

/* eslint-disable @typescript-eslint/no-explicit-any */
function summarize(name: string, raw: string, data: any): string {
  if (name === 'get_policy_wording') {
    const chars = raw.match(/(\d+) characters/)?.[1]
    return `policy wording read${chars ? ` (${Number(chars).toLocaleString('en-US')} characters)` : ''}`
  }
  if (data === undefined) return raw.length > 100 ? `${raw.slice(0, 100)}…` : raw
  switch (name) {
    case 'get_claim':
      return `${data.claim_id} · ${humanize(data.claim_type)} · ${formatUsd(data.amount_claimed)} claimed`
    case 'get_policy':
      return `${data.plan} plan · ${data.policy_id} · ${data.trip?.destination}`
    case 'get_customer_history':
      return `${data.name} · ${plural(data.previous_claims?.length ?? 0, 'earlier claim')}`
    case 'get_plan':
      return `${data.plan?.plan} plan limits${data.add_ons?.length ? ` + ${plural(data.add_ons.length, 'add-on')}` : ''}`
    case 'lookup_knowledge':
      return plural(Array.isArray(data) ? data.length : 0, 'fact')
    case 'calculate_payout':
      return `payout ${formatUsd(data.payout)} (${plural(data.steps?.length ?? 0, 'step')})`
    case 'request_payment':
      return `payment ${data.status} · ${formatUsd(data.amount)}`
    default:
      return raw.slice(0, 100)
  }
}

function ToolResult({ name, raw }: { name: string; raw: string }) {
  const [open, setOpen] = useState(false)
  const data = parseJson(raw)
  const detailed = data !== undefined && name !== 'get_policy_wording'
  return (
    <span className="body">
      <span>
        {name} → {summarize(name, raw, data)}
      </span>
      {detailed && (
        <>
          {' '}
          <button className="linkbtn" onClick={() => setOpen(!open)} aria-expanded={open}>
            {open ? 'Hide details' : 'Show details'}
          </button>
          {open && <pre className="json">{JSON.stringify(data, null, 2)}</pre>}
        </>
      )}
    </span>
  )
}

function Row({ event }: { event: TraceEvent }) {
  const c = event.content as Record<string, any>
  const label = event.kind.replace(/_/g, ' ')
  let body
  switch (event.kind) {
    case 'tool_result':
      body = <ToolResult name={c.name} raw={String(c.content)} />
      break
    case 'tool_call':
      body = (
        <span className="body">
          {c.name}
          {Object.keys(c.args ?? {}).length > 0 && <span className="muted"> {JSON.stringify(c.args)}</span>}
        </span>
      )
      break
    case 'status':
      body = (
        <span className="body">
          {String(c.status).replace(/_/g, ' ')}
          {c.model && <span className="muted"> · model {c.model}</span>}
        </span>
      )
      break
    case 'decision':
      body = (
        <span className="body">
          <strong>{c.decision}</strong>
          {c.decision === 'approve' && ` ${formatUsd(c.payout)}`} — {c.rationale}
        </span>
      )
      break
    case 'usage':
      body = <span className="body">{describeUsage(c as Parameters<typeof describeUsage>[0])}</span>
      break
    case 'message':
      body = <span className="body">{String(c.text)}</span>
      break
    default:
      body = <span className="body">{String(c.message ?? JSON.stringify(c))}</span>
  }
  return (
    <li className={`trace-${event.kind}`}>
      <span className="kind">{label}</span>
      {body}
    </li>
  )
}

export default function Trace({ claimId, status }: { claimId: string; status: string }) {
  const [events, setEvents] = useState<TraceEvent[] | null>(null)
  const [expanded, setExpanded] = useState(true)

  useEffect(() => {
    let alive = true
    const load = () =>
      getTrace(claimId)
        .then((e) => alive && setEvents(e))
        .catch(() => {})
    void load()
    // poll only while the agent is working on the claim
    const timer = status === 'in_progress' ? setInterval(load, POLL_MS) : undefined
    return () => {
      alive = false
      if (timer) clearInterval(timer)
    }
  }, [claimId, status])

  return (
    <div className="card pad">
      <div className="trace-head">
        <h3>
          Agent activity
          {events && events.length > 0 && <span className="muted small"> · {plural(events.length, 'step')}</span>}
        </h3>
        <button className="btn btn-secondary btn-sm" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>
          {expanded ? 'Collapse activity' : 'Expand activity'}
        </button>
      </div>
      {expanded && events && events.length === 0 && <p className="muted">No activity yet.</p>}
      {expanded && events && events.length > 0 && (
        <ol className="trace">
          {events.map((e) => (
            <Row key={e.seq} event={e} />
          ))}
        </ol>
      )}
    </div>
  )
}
