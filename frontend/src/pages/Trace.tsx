import { useEffect, useState } from 'react'
import { getTrace, type TraceEvent } from '../api'
import { formatUsd } from '../format'

const POLL_MS = 1000

function describe(event: TraceEvent): string {
  const c = event.content as Record<string, any>
  switch (event.kind) {
    case 'status':
      return `${String(c.status).replace(/_/g, ' ')}${c.model ? ` · model ${c.model}` : ''}`
    case 'tool_call':
      return `${c.name}(${Object.keys(c.args ?? {}).length ? JSON.stringify(c.args) : ''})`
    case 'tool_result':
      return `${c.name} → ${c.content}`
    case 'message':
      return String(c.text)
    case 'decision':
      return `${c.decision}${c.decision === 'approve' ? ` ${formatUsd(c.payout)}` : ''} — ${c.rationale}`
    default:
      return String(c.message ?? JSON.stringify(c))
  }
}

export default function Trace({ claimId, status }: { claimId: string; status: string }) {
  const [events, setEvents] = useState<TraceEvent[] | null>(null)

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
      <h3>Agent activity</h3>
      {events && events.length === 0 && <p className="muted">No activity yet.</p>}
      {events && events.length > 0 && (
        <ol className="trace">
          {events.map((e) => (
            <li key={e.seq}>
              <span className="kind">{e.kind.replace(/_/g, ' ')}</span>
              <span className="body">{describe(e)}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
