import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mockApi, signedIn } from '../testUtils'
import { CARD, conversation, step, turn } from './fixtures'
import Sidebar from './Sidebar'

const READ = '/api/copilot/conversations/c-1'

function show() {
  return render(<MemoryRouter>{signedIn(<Sidebar />)}</MemoryRouter>)
}

beforeEach(() => localStorage.clear())
afterEach(() => vi.useRealTimers())

describe('the Copilot sidebar', () => {
  it('is open the first time, collapses to a tab, and stays collapsed after a reload', async () => {
    mockApi({ '/api/copilot/conversations': [] })
    const first = show()
    expect(await screen.findByLabelText('Ask the Copilot')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Collapse the Copilot' }))
    expect(screen.queryByLabelText('Ask the Copilot')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open the Copilot' })).toBeInTheDocument()
    first.unmount()
    show() // a reload, in the same browser
    expect(screen.getByRole('button', { name: 'Open the Copilot' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Ask the Copilot')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Open the Copilot' }))
    expect(await screen.findByLabelText('Ask the Copilot')).toBeInTheDocument()
  })

  it('greets the handler by first name and offers suggested questions to click', async () => {
    const { calls } = mockApi({
      '/api/copilot/conversations': [],
      'POST /api/copilot/conversations': { conversation_id: 'c-1' },
      'POST /api/copilot/conversations/c-1/messages': { turn_id: 1 },
      [READ]: conversation([turn({ status: 'working', answer: null, message: 'What needs me today?' })]),
    })
    show()
    expect(await screen.findByText('Hi Dana')).toBeInTheDocument()
    const suggestions = screen.getAllByRole('button').filter((b) => b.closest('.copilot-suggestions'))
    expect(suggestions).toHaveLength(3)
    await userEvent.click(screen.getByRole('button', { name: 'What needs me today?' }))
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/messages'))).toBe(true))
    expect(calls.find((c) => c.url.endsWith('/messages'))!.body).toEqual({ text: 'What needs me today?' })
  })

  it('shows the first message of a new conversation as soon as it is sent', async () => {
    let sent = false
    mockApi({
      '/api/copilot/conversations': [],
      'POST /api/copilot/conversations': { conversation_id: 'c-1' },
      'POST /api/copilot/conversations/c-1/messages': () => ((sent = true), { turn_id: 1 }),
      [READ]: () => conversation(sent ? [turn({ status: 'working', answer: null, message: 'hello there' })] : []), // empty until the message is sent
    })
    show()
    await userEvent.type(await screen.findByLabelText('Ask the Copilot'), 'hello there{Enter}')
    expect(await screen.findByText('hello there')).toBeInTheDocument()
    expect(screen.getByRole('status', { name: 'The Copilot is working' })).toBeInTheDocument()
  })

  it('reads the conversation again when sending fails, since the message may have gone through (a 504)', async () => {
    localStorage.setItem('copilot.conversation', 'c-1')
    let working = false
    mockApi({
      '/api/copilot/conversations': [],
      [READ]: () => conversation(working ? [turn({ status: 'working', answer: null, message: 'hello' })] : [turn()]),
      'POST /api/copilot/conversations/c-1/messages': () => {
        working = true // the Copilot took it, but the answer never got back
        return { status: 504, body: { detail: 'The Copilot did not answer in time. Reload the page to see whether it went through.' } }
      },
    })
    show()
    await userEvent.type(await screen.findByLabelText('Ask the Copilot'), 'hello{Enter}')
    expect(await screen.findByText(/did not answer in time/)).toBeInTheDocument()
    expect(await screen.findByRole('status', { name: 'The Copilot is working' })).toBeInTheDocument() // what is true, not what was shown before
    expect(screen.getByLabelText('Ask the Copilot')).toBeDisabled()
  })

  it('sends on Enter, adds a line on Shift+Enter, and is disabled while the Copilot works', async () => {
    const { calls } = mockApi({
      '/api/copilot/conversations': [],
      'POST /api/copilot/conversations': { conversation_id: 'c-1' },
      'POST /api/copilot/conversations/c-1/messages': { turn_id: 1 },
      [READ]: conversation([turn({ status: 'working', answer: null, message: 'one\ntwo' })]),
    })
    show()
    const box = await screen.findByLabelText('Ask the Copilot')
    await userEvent.type(box, 'one{Shift>}{Enter}{/Shift}two')
    expect(box).toHaveValue('one\ntwo')
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0)
    await userEvent.type(box, '{Enter}')
    await waitFor(() => expect(calls.find((c) => c.url.endsWith('/messages'))!.body).toEqual({ text: 'one\ntwo' }))
    await waitFor(() => expect(screen.getByLabelText('Ask the Copilot')).toBeDisabled())
    expect(screen.getByRole('status', { name: 'The Copilot is working' })).toBeInTheDocument()
  })

  it('shows the confirmation, asks for it first, and sends the answer', async () => {
    localStorage.setItem('copilot.conversation', 'c-1')
    const waiting = conversation([turn({ status: 'waiting', answer: null, confirmation: CARD, steps: [step({ tool: 'sign_off', kind: 'action', main_input: 'CLM-1003' })] })])
    const { calls } = mockApi({
      '/api/copilot/conversations': [{ conversation_id: 'c-1', title: 'What needs me?', updated_at: '2026-01-01T00:00:00Z' }],
      [READ]: waiting,
      'POST /api/copilot/conversations/c-1/confirmation': { turn_id: 1 },
    })
    show()
    const card = await screen.findByRole('region', { name: 'Confirm the sign-off' })
    expect(within(card).getByText('$3,750.00')).toBeInTheDocument()
    expect(within(card).getByText('Maya Patel')).toBeInTheDocument()
    expect(within(card).getByText('Covered under section 10.')).toBeInTheDocument()
    const box = screen.getByLabelText('Ask the Copilot')
    expect(box).toBeDisabled()
    expect(box).toHaveAttribute('placeholder', 'Answer the confirmation above first')
    await userEvent.click(within(card).getByRole('button', { name: 'Sign off and pay' }))
    await waitFor(() => expect(calls.find((c) => c.url.endsWith('/confirmation'))!.body).toEqual({ answer: 'sign_off' }))
  })

  it('sends cancel when the handler cancels', async () => {
    localStorage.setItem('copilot.conversation', 'c-1')
    const { calls } = mockApi({
      '/api/copilot/conversations': [],
      [READ]: conversation([turn({ status: 'waiting', answer: null, confirmation: CARD })]),
      'POST /api/copilot/conversations/c-1/confirmation': { turn_id: 1 },
    })
    show()
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(calls.find((c) => c.url.endsWith('/confirmation'))!.body).toEqual({ answer: 'cancel' }))
  })

  it('polls every second while a turn works, and not when the conversation is idle', async () => {
    localStorage.setItem('copilot.conversation', 'c-1')
    let state = 'working'
    const { calls } = mockApi({
      '/api/copilot/conversations': [],
      [READ]: () => conversation([turn({ status: state as 'working' | 'done', answer: state === 'done' ? 'Done.' : null })]),
    })
    vi.useFakeTimers()
    show()
    const reads = () => calls.filter((c) => c.url === READ).length
    await act(async () => { await vi.advanceTimersByTimeAsync(10) })
    const afterFirst = reads()
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(reads() - afterFirst).toBeGreaterThanOrEqual(3) // about once a second
    state = 'done'
    await act(async () => { await vi.advanceTimersByTimeAsync(1100) })
    const idle = reads()
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(reads()).toBe(idle) // the turn is done: no more reads
    expect(screen.getByText('Done.')).toBeInTheDocument()
  })

  it('lets a new message through after a failed turn and after a stopped one', async () => {
    localStorage.setItem('copilot.conversation', 'c-1')
    mockApi({
      '/api/copilot/conversations': [],
      [READ]: conversation([turn({ status: 'failed', answer: null, error: 'TooManyModelCalls: 25' }), turn({ turn_id: 2, status: 'stopped', answer: null })]),
    })
    show()
    expect(await screen.findByText(/Something went wrong: TooManyModelCalls: 25/)).toBeInTheDocument()
    expect(screen.getByText(/This turn stopped/)).toBeInTheDocument()
    expect(screen.getByLabelText('Ask the Copilot')).toBeEnabled()
  })

  it('shows the turn\'s model calls, cost and time in small print under the answer', async () => {
    localStorage.setItem('copilot.conversation', 'c-1')
    mockApi({ '/api/copilot/conversations': [], [READ]: conversation([turn({ model_calls: 3, cost_usd: 0.0123, seconds: 14.4 })]) })
    show()
    expect(await screen.findByText('3 model calls · $0.0123 · 14 s')).toBeInTheDocument()
  })

  it('lists earlier conversations by their first question and opens one', async () => {
    mockApi({
      '/api/copilot/conversations': [
        { conversation_id: 'c-2', title: 'Why did CLM-1012 need me?', updated_at: '2026-01-02T00:00:00Z' },
        { conversation_id: 'c-1', title: 'What needs me?', updated_at: '2026-01-01T00:00:00Z' },
      ],
      '/api/copilot/conversations/c-2': conversation([turn({ message: 'Why did CLM-1012 need me?', answer: 'Because it was held.' })], 'c-2'),
    })
    show()
    await userEvent.click(await screen.findByText('Earlier conversations (2)'))
    const titles = screen.getAllByRole('button').filter((b) => b.closest('.copilot-earlier li')).map((b) => b.textContent)
    expect(titles).toEqual(['Why did CLM-1012 need me?', 'What needs me?'])
    await userEvent.click(screen.getByRole('button', { name: 'Why did CLM-1012 need me?' }))
    expect(await screen.findByText('Because it was held.')).toBeInTheDocument()
  })

  it('says who is signed in at the foot', async () => {
    mockApi({ '/api/copilot/conversations': [] })
    show()
    expect(await screen.findByText('Signed in as Dana Whitfield')).toBeInTheDocument()
  })
})
