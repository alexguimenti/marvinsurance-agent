import type { ReactNode } from 'react'
import type { StepKind } from './api'

// Small line icons, drawn on a 16 px grid in the current text color, so they follow light and dark mode.
function Icon({ children, label }: { children: ReactNode; label?: string }) {
  return (
    <svg className="cp-icon" viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.4"
      strokeLinecap="round" strokeLinejoin="round" role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      {children}
    </svg>
  )
}

/** The Copilot's own mark: a speech bubble with a spark, on the slim tab and at the top of the sidebar. */
export function CopilotIcon({ label }: { label?: string }) {
  return (
    <Icon label={label}>
      <path d="M2.5 3.5h11a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H7l-3 2.5v-2.5H2.5a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1Z" />
      <path d="M8 5.2v3.6M6.2 7h3.6" />
    </Icon>
  )
}

const KIND_ICONS: Record<StepKind, ReactNode> = {
  claims: (
    <>
      <path d="M4 1.8h5.5L12.5 5v9.2H4Z" />
      <path d="M9.2 1.8V5h3.3M6 8h4.5M6 10.6h4.5" />
    </>
  ),
  customers: (
    <>
      <circle cx="8" cy="5.4" r="2.4" />
      <path d="M3 13.8c.4-2.6 2.5-4 5-4s4.6 1.4 5 4" />
    </>
  ),
  context: (
    <>
      <path d="M2.2 3.2c1.8-.6 3.7-.5 5.8.8 2.1-1.3 4-1.4 5.8-.8v9c-1.8-.6-3.7-.5-5.8.8-2.1-1.3-4-1.4-5.8-.8Z" />
      <path d="M8 4v9" />
    </>
  ),
  work: (
    <>
      <circle cx="8" cy="8" r="2" />
      <path d="M8 1.6v2M8 12.4v2M1.6 8h2M12.4 8h2M3.5 3.5l1.4 1.4M11.1 11.1l1.4 1.4M12.5 3.5l-1.4 1.4M4.9 11.1l-1.4 1.4" />
    </>
  ),
  memory: (
    <>
      <path d="M5.2 2.6a2.6 2.6 0 0 0-2.4 3.2 2.5 2.5 0 0 0 .6 4.5 2.4 2.4 0 0 0 4.6.9V3.6a1.9 1.9 0 0 0-2.8-1Z" />
      <path d="M10.8 2.6a2.6 2.6 0 0 1 2.4 3.2 2.5 2.5 0 0 1-.6 4.5 2.4 2.4 0 0 1-4.6.9" />
    </>
  ),
  action: <path d="M9.2 1.6 3 9h4.4l-.6 5.4L13 7H8.6Z" />,
}

export function KindIcon({ kind }: { kind: StepKind }) {
  return <Icon>{KIND_ICONS[kind]}</Icon>
}

export function Spinner() {
  return (
    <svg className="cp-spinner" viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <path d="M8 1.8a6.2 6.2 0 1 1-6.2 6.2" />
    </svg>
  )
}

export function Chevron() {
  return (
    <svg className="cp-chevron" viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m5 6 3 3 3-3" />
    </svg>
  )
}
