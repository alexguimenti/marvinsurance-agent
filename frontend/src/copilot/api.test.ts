import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, setAuth } from '../api'
import { mockApi } from '../testUtils'
import { answerConfirmation, createConversation, getConversation, sendMessage } from './api'

afterEach(() => setAuth(null))

function signedInWith(getToken: string, getFreshToken: string) {
  const calls = { plain: 0, fresh: 0 }
  setAuth({
    getToken: async () => (calls.plain++, getToken),
    getFreshToken: async () => (calls.fresh++, getFreshToken),
    onUnauthorized: vi.fn(),
  })
  return calls
}

describe('the Copilot API', () => {
  it('sends a message and an answer with a renewed sign-in, and reads with the ordinary one', async () => {
    const calls = signedInWith('plain-token', 'fresh-token')
    const { fetchMock } = mockApi({
      'POST /api/copilot/conversations': { conversation_id: 'c-1' },
      '/api/copilot/conversations/c-1': { conversation_id: 'c-1', title: '', turns: [] },
      'POST /api/copilot/conversations/c-1/messages': { turn_id: 1 },
      'POST /api/copilot/conversations/c-1/confirmation': { turn_id: 1 },
    })
    await createConversation()
    await getConversation('c-1')
    expect(calls).toEqual({ plain: 2, fresh: 0 })
    await sendMessage('c-1', 'hello')
    await answerConfirmation('c-1', 'cancel')
    expect(calls).toEqual({ plain: 2, fresh: 2 })
    const auths = fetchMock.mock.calls.map(([, init]) => new Headers(init?.headers).get('Authorization'))
    expect(auths).toEqual(['Bearer plain-token', 'Bearer plain-token', 'Bearer fresh-token', 'Bearer fresh-token'])
  })

  it('surfaces the reason of a 409', async () => {
    signedInWith('t', 't')
    mockApi({ 'POST /api/copilot/conversations/c-1/messages': { status: 409, body: { detail: 'the Copilot is still working on your last message' } } })
    const error = await sendMessage('c-1', 'again').catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(409)
    expect(error.message).toBe('the Copilot is still working on your last message')
  })
})
