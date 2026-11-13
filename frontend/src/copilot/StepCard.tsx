import { useEffect, useState } from 'react'
import type { Step } from './api'
import { Chevron, KindIcon, Spinner } from './icons'
import { KIND_LABEL } from './kinds'

const TRIM = 1200

/** Seconds since an ISO time, re-read every second while `running`. */
function useElapsed(since: string, running: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [running])
  return Math.max(0, Math.floor((now - Date.parse(since)) / 1000))
}

function trimmed(text: string): string {
  return text.length <= TRIM ? text : `${text.slice(0, TRIM)}\n… ${text.length - TRIM} more characters`
}

/** What a card shows when it is opened, JSON made readable when it is JSON. */
function readable(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return text
  }
}

const ACTION_TOOLS = new Set(['start_dispatch', 'start_evaluation', 'send_back', 'sign_off'])

/** One tool the Copilot called: a compact card that opens to show what was asked and what came back. */
export default function StepCard({ step }: { step: Step }) {
  const [open, setOpen] = useState(false)
  const elapsed = useElapsed(step.started_at, step.status === 'working')
  const changes = step.kind === 'action' || ACTION_TOOLS.has(step.tool)
  const state =
    step.status === 'working' ? (
      <span className="cp-state cp-state--working"><Spinner /> {elapsed} s</span>
    ) : step.status === 'done' ? (
      <span className="cp-state cp-state--done">done · {(step.seconds ?? 0).toFixed(1)} s</span>
    ) : (
      <span className={`cp-state cp-state--${step.status}`}>{step.status}</span>
    )
  return (
    <div className={`cp-step${changes ? ' cp-step--action' : ''} cp-step--${step.status}`} data-kind={step.kind}>
      <button type="button" className="cp-step-head" aria-expanded={open} onClick={() => setOpen(!open)} title={KIND_LABEL[step.kind]}>
        <KindIcon kind={step.kind} />
        <span className="cp-step-tool">{step.tool}</span>
        {step.main_input && <span className="cp-step-input">{step.main_input}</span>}
        {state}
        <Chevron />
      </button>
      {step.status !== 'working' && step.status !== 'done' && step.reason && !open && <p className="cp-step-reason small">{step.reason}</p>}
      {open && (
        <div className="cp-step-body">
          <p className="cp-step-label">Asked</p>
          <pre>{step.input && Object.keys(step.input).length ? JSON.stringify(step.input, null, 2) : 'nothing needed'}</pre>
          <p className="cp-step-label">Came back</p>
          {step.status === 'working' ? (
            <pre>still working</pre>
          ) : (
            <pre>{step.output ? trimmed(readable(step.output)) : (step.reason ?? 'nothing')}</pre>
          )}
          {step.sources?.map((source) => (
            <p key={source.file} className="cp-step-source small muted">
              From <code>{source.file}</code>, owned by {source.owner}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}
