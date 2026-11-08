import type { ReactNode } from 'react'

/** The head of every page: what the page is, what it is for in a sentence, and the actions that belong to it. */
export default function PageHeader({ eyebrow, title, lede, actions }: { eyebrow: ReactNode; title: ReactNode; lede: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p className="lede">{lede}</p>
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  )
}
