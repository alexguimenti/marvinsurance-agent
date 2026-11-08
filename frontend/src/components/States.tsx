import type { ReactNode } from 'react'

/** Loading, empty and error states: each says what is happening or what to do next, and keeps the page's shape. */
export function Loading({ what }: { what: string }) {
  return (
    <p className="state state-loading muted" role="status">
      Loading {what}
    </p>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="state state-empty empty">{children}</p>
}

export function ErrorState({ children, onRetry }: { children: ReactNode; onRetry?: () => void }) {
  return (
    <div className="state state-error error" role="alert">
      <span>{children}</span>
      {onRetry && (
        <button className="btn btn-secondary btn-sm" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}
