import { useCallback, useState } from 'react'
import { usePolling } from '../usePolling'
import { getConversation, type Conversation } from './api'

export const POLL_MS = 1000 // new steps are checked for about once a second while the Copilot works on a turn, and not while it is idle

interface Read {
  id: string | null
  conversation: Conversation | null
  error: string | null
}

/** A conversation, kept up to date while its last turn is working: read once, then every second until the turn is no longer working. */
export function useConversation(id: string | null) {
  const [read, setRead] = useState<Read>({ id: null, conversation: null, error: null })
  const mine = read.id === id // what was read for another conversation is not shown for this one
  const conversation = mine ? read.conversation : null

  // `of` is for a conversation just made: this hook's `id` is still the old one until the next render
  const load = useCallback(async (of: string | null = id) => {
    if (!of) return
    try {
      const found = await getConversation(of)
      setRead({ id: of, conversation: found, error: null })
    } catch (e) {
      setRead((current) => ({ id: of, conversation: current.id === of ? current.conversation : null, error: e instanceof Error ? e.message : 'The conversation could not be read.' }))
    }
  }, [id])

  const working = conversation?.turns.at(-1)?.status === 'working'
  const poll = useCallback(() => load(), [load])
  usePolling(poll, id && working ? POLL_MS : null)
  return { conversation, error: mine ? read.error : null, reload: load }
}
