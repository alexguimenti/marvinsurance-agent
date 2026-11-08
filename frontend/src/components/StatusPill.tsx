import type { ReactNode } from 'react'

export type Tone = 'success' | 'danger' | 'warning' | 'info' | 'neutral'

/** A status shown with a color and a word: the word carries the meaning, the color helps the eye find it. */
export default function StatusPill({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`pill pill-tone-${tone}`}>{children}</span>
}
