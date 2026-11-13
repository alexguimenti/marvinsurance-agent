import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { ApiError } from '../api'
import { useAuth } from '../auth/context'
import { answerConfirmation, createConversation, listConversations, sendMessage, type ConversationSummary } from './api'
import ConversationView from './ConversationView'
import { CopilotIcon } from './icons'
import { useConversation } from './useConversation'
import './copilot.css'

const OPEN_KEY = 'copilot.open' // the handler's choice, kept in this browser
const ACTIVE_KEY = 'copilot.conversation'
const SUGGESTIONS = [
  'What needs me today?',
  'Work the queue with 10 agents and tell me what needs me.',
  'Are we meeting the success criteria we agreed?',
]

function remembered(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function remember(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    /* a browser that keeps nothing: the sidebar still works */
  }
}

/** The Copilot, in a sidebar on the right of every signed-in page. Open the first time a handler signs in; collapsed it is a slim tab. */
export default function Sidebar() {
  const { handler } = useAuth()
  const [open, setOpen] = useState(() => remembered(OPEN_KEY) !== 'false')
  const [activeId, setActiveId] = useState<string | null>(() => remembered(ACTIVE_KEY))
  const [earlier, setEarlier] = useState<ConversationSummary[]>([])
  const [text, setText] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const { conversation, error, reload } = useConversation(activeId)
  const end = useRef<HTMLDivElement>(null)

  const turns = conversation?.turns ?? []
  const last = turns.at(-1)
  const working = last?.status === 'working'
  const waiting = last?.status === 'waiting'

  const loadEarlier = useCallback(() => {
    listConversations().then(setEarlier).catch(() => {})
  }, [])
  useEffect(loadEarlier, [loadEarlier])
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: 'end' })
  }, [turns.length, last?.steps.length, last?.status])
  useEffect(() => {
    if (error && /unknown conversation/.test(error)) choose(null) // a conversation another handler remembered in this browser
  }, [error]) // eslint-disable-line react-hooks/exhaustive-deps

  function toggle(next: boolean) {
    setOpen(next)
    remember(OPEN_KEY, String(next))
  }

  function choose(id: string | null) {
    setActiveId(id)
    remember(ACTIVE_KEY, id)
    setProblem(null)
  }

  async function send(message: string) {
    const asked = message.trim()
    if (!asked || sending || working || waiting) return
    setSending(true)
    setProblem(null)
    try {
      const id = activeId ?? (await createConversation()).conversation_id
      if (id !== activeId) choose(id)
      await sendMessage(id, asked)
      setText('')
      await reload(id)
      loadEarlier()
    } catch (e) {
      setProblem(e instanceof ApiError || e instanceof Error ? e.message : 'The message was not sent.')
    } finally {
      setSending(false)
    }
  }

  async function answer(choice: 'sign_off' | 'cancel') {
    if (!activeId) return
    setSending(true)
    setProblem(null)
    try {
      await answerConfirmation(activeId, choice)
      await reload()
    } catch (e) {
      setProblem(e instanceof Error ? e.message : 'The answer was not sent.')
    } finally {
      setSending(false)
    }
  }

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void send(text)
    }
  }

  if (!open) {
    return (
      <aside className="copilot copilot--closed" aria-label="Copilot">
        <button type="button" className="copilot-tab" onClick={() => toggle(true)} aria-label="Open the Copilot" title="Open the Copilot">
          <CopilotIcon />
          <span>Copilot</span>
        </button>
      </aside>
    )
  }

  const first = handler.name.split(' ')[0]
  return (
    <aside className="copilot" aria-label="Copilot">
      <header className="copilot-head">
        <span className="copilot-title"><CopilotIcon /> Copilot</span>
        <span className="copilot-head-actions">
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => choose(null)}>New conversation</button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => toggle(false)} aria-label="Collapse the Copilot" title="Collapse">×</button>
        </span>
      </header>
      {earlier.length > 0 && (
        <details className="copilot-earlier">
          <summary>Earlier conversations ({earlier.length})</summary>
          <ul>
            {earlier.map((c) => (
              <li key={c.conversation_id}>
                <button type="button" className={c.conversation_id === activeId ? 'is-active' : ''} onClick={() => choose(c.conversation_id)}>
                  {c.title || 'New conversation'}
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className="copilot-body" role="log" aria-live="polite">
        {turns.length === 0 ? (
          <div className="copilot-empty">
            <p className="copilot-hello">Hi {first}</p>
            <p className="muted small">Ask in plain English. I look things up, start the agent's work, send decisions back and sign them off, as you.</p>
            <div className="copilot-suggestions">
              {SUGGESTIONS.map((s) => (
                <button type="button" key={s} onClick={() => void send(s)} disabled={sending}>{s}</button>
              ))}
            </div>
          </div>
        ) : (
          <ConversationView turns={turns} onAnswer={(choice) => void answer(choice)} answering={sending} />
        )}
        {error && <p className="cp-problem" role="alert">{error}</p>}
        <div ref={end} />
      </div>
      <footer className="copilot-foot">
        {problem && <p className="cp-problem" role="alert">{problem}</p>}
        <textarea
          aria-label="Ask the Copilot"
          placeholder={waiting ? 'Answer the confirmation above first' : 'Ask the Copilot'}
          rows={2}
          value={text}
          disabled={working || waiting || sending}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
        />
        <p className="copilot-who small muted">Signed in as {handler.name}</p>
      </footer>
    </aside>
  )
}
