import { useCallback, useState, type ReactNode } from 'react'
import { getTrace, type Source, type TraceEvent } from '../api'
import { DOCUMENT_LABEL, describeLookup, describeUsage, formatUsd, humanize } from '../format'
import { usePolling } from '../usePolling'

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
const docName = (doc: string) => (DOCUMENT_LABEL[doc] ?? doc).toLowerCase()

/** A section the agent read is traced as "[claims_manual section 7: Interpretation notes, 3120 characters]". */
const SECTION_READ = /^\[(\w+) section (\d+): (.+), (\d+) characters\]$/

function summarize(name: string, raw: string, data: any): string {
  const read = name === 'read_section' ? raw.match(SECTION_READ) : null
  if (read) {
    return `${docName(read[1])} §${read[2]} ${read[3]} (${Number(read[4]).toLocaleString('en-US')} characters)`
  }
  if (data === undefined) return raw.length > 100 ? `${raw.slice(0, 100)}…` : raw
  switch (name) {
    case 'get_claim':
      return `${data.claim_id} · ${humanize(data.claim_type)} · ${formatUsd(data.amount_claimed)} claimed`
    case 'get_policy':
      return `${data.plan} plan · ${data.policy_id} · ${data.trip?.destination}`
    case 'get_claim_history':
      return `${data.name} · ${plural(data.previous_claims?.length ?? 0, 'earlier claim')}`
    case 'get_plan':
      return `${data.plan?.plan} plan limits${data.add_ons?.length ? ` + ${plural(data.add_ons.length, 'add-on')}` : ''}`
    case 'search_documents':
      return `${plural(Array.isArray(data) ? data.length : 0, 'section')} found`
    case 'list_sections':
      return plural(Array.isArray(data) ? data.length : 0, 'section')
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
  const detailed = data !== undefined
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

function describeCall(name: string, args: Record<string, any>): ReactNode {
  if (name === 'read_section') return `Read ${docName(args.document)}, section ${args.section}`
  if (name === 'list_sections') return `List the sections of the ${docName(args.document)}`
  if (name === 'search_documents') return `Search the wording and the manual for "${args.query}"`
  return (
    <>
      {name}
      {Object.keys(args).length > 0 && <span className="muted"> {JSON.stringify(args)}</span>}
    </>
  )
}

const REFUSER: Record<string, string> = { gateway: 'the Gateway', app: 'the app', context: 'the context layer', memory: 'memory' }

function Row({ event }: { event: TraceEvent }) {
  const c = event.content as Record<string, any>
  const label = event.kind === 'refusal' ? `Refused by ${REFUSER[c.by] ?? c.by}` : event.kind.replace(/_/g, ' ')
  let body
  switch (event.kind) {
    case 'tool_result':
      body = <ToolResult name={c.name} raw={String(c.content)} />
      break
    case 'tool_call':
      body = <span className="body">{describeCall(c.name, c.args ?? {})}</span>
      break
    case 'lookup':
      body = (
        <span className="body">
          <span>Looked up {describeLookup(c.tool, c.args ?? {})}</span> <span className="muted small">via {c.tool}</span>
          {c.found && <span className="muted"> — {String(c.found)}</span>}
          {((c.sources ?? []) as Source[]).map((s) => (
            <span key={s.file} className="muted small source">
              {`${s.file} · ${s.system} · ${s.owner} · ${s.reviewed}`}
            </span>
          ))}
        </span>
      )
      break
    case 'memory_search':
      body = (
        <span className="body">
          <span>Searched memory for "{c.query}"</span>
          {(c.found ?? []).length === 0 ? (
            <span className="muted"> — nothing found</span>
          ) : (
            (c.found as string[]).map((f, i) => (
              <span key={i} className="muted small source">
                {f}
              </span>
            ))
          )}
        </span>
      )
      break
    case 'memory_save':
      body = (
        <span className="body">
          {c.error ? <span>Could not save to memory — {String(c.error)}</span> : <span>Saved to memory</span>}
          {((c.saved ?? []) as string[]).map((f, i) => (
            <span key={i} className="muted small source">
              {f}
            </span>
          ))}
        </span>
      )
      break
    case 'refusal':
      body = (
        <span className="body">
          <span className="muted small">{c.tool}</span> <span>{String(c.reason)}</span>
        </span>
      )
      break
    case 'status':
      body = (
        <span className="body">
          {String(c.status).replace(/_/g, ' ')}
          {c.attempt > 1 && <span className="muted"> · attempt {c.attempt}</span>}
          {c.model && <span className="muted"> · model {c.model}</span>}
          {c.note && <span className="muted"> · {c.note}</span>}
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
    case 'screening':
      body = (
        <span className="body">
          <strong>{c.instructions_found ? 'The security check found an instruction' : 'The security check found nothing'}</strong>
          {' '}— {c.reason}
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

  const load = useCallback(
    () =>
      getTrace(claimId)
        .then(setEvents)
        .catch(() => {}),
    [claimId],
  )
  // poll only while the agent is working on the claim; load once more when the status changes
  const interval = status === 'in_progress' ? POLL_MS : null
  usePolling(load, interval)

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
