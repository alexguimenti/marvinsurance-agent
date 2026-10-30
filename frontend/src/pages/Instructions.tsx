import { Fragment, useEffect, useState, type ReactNode } from 'react'
import { getInstructions } from '../api'

/** `code` spans only; the instructions use nothing else inline. */
function inline(text: string): ReactNode[] {
  return text.split(/`([^`]+)`/).map((part, i) => (i % 2 ? <code key={i}>{part}</code> : <Fragment key={i}>{part}</Fragment>))
}

interface Item {
  text: string
  sub: string[] // indented bullets under this item
}

/** A small markdown reader for the agent's instructions: ## headings, 1. lists, - lists (nestable) and paragraphs. */
function renderMarkdown(text: string): ReactNode[] {
  const blocks: ReactNode[] = []
  let list: { ordered: boolean; items: Item[] } | null = null
  let paragraph: string[] = []

  const flushParagraph = () => {
    if (paragraph.length) blocks.push(<p key={blocks.length}>{inline(paragraph.join(' '))}</p>)
    paragraph = []
  }
  const flushList = () => {
    if (!list) return
    const Tag = list.ordered ? 'ol' : 'ul'
    blocks.push(
      <Tag key={blocks.length}>
        {list.items.map((item, i) => (
          <li key={i}>
            {inline(item.text)}
            {item.sub.length > 0 && (
              <ul>
                {item.sub.map((sub, j) => (
                  <li key={j}>{inline(sub)}</li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </Tag>,
    )
    list = null
  }

  for (const line of text.split('\n')) {
    const heading = line.match(/^##\s+(.*)/)
    const ordered = line.match(/^\s*\d+\.\s+(.*)/)
    const bullet = line.match(/^(\s*)-\s+(.*)/)
    if (heading) {
      flushParagraph()
      flushList()
      blocks.push(<h2 key={blocks.length}>{heading[1]}</h2>)
    } else if (bullet && bullet[1].length >= 2 && list && list.items.length) {
      list.items[list.items.length - 1].sub.push(bullet[2]) // an indented bullet belongs to the item above it
    } else if (ordered || bullet) {
      flushParagraph()
      const isOrdered = Boolean(ordered)
      if (list && list.ordered !== isOrdered) flushList()
      list ??= { ordered: isOrdered, items: [] }
      list.items.push({ text: (ordered ? ordered[1] : bullet![2]), sub: [] })
    } else if (line.trim() === '') {
      flushParagraph()
      flushList()
    } else if (list && /^\s+/.test(line)) {
      const last = list.items[list.items.length - 1]
      if (last.sub.length) last.sub[last.sub.length - 1] += ` ${line.trim()}` // a wrapped bullet
      else last.text += ` ${line.trim()}` // a wrapped item
    } else {
      flushList()
      paragraph.push(line.trim())
    }
  }
  flushParagraph()
  flushList()
  return blocks
}

export default function Instructions() {
  const [texts, setTexts] = useState<{ run: string; claim: string } | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    getInstructions()
      .then(setTexts)
      .catch(() => setError(true))
  }, [])

  return (
    <section>
      <div className="page-head">
        <div>
          <p className="eyebrow">Standing orders</p>
          <h1>Agent instructions</h1>
          <p className="lede">
            The briefs the Farting Agent works from: one for running the queue, one for each claim it is handed.
          </p>
        </div>
      </div>
      {error && <p className="error">Could not load the instructions.</p>}
      {texts !== null && (
        <>
          <h2 className="section-title">Running the queue</h2>
          <article className="card pad instructions">{renderMarkdown(texts.run)}</article>
          <h2 className="section-title">Working a claim</h2>
          <article className="card pad instructions">{renderMarkdown(texts.claim)}</article>
        </>
      )}
    </section>
  )
}
