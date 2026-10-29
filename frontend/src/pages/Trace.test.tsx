import { render, screen, waitFor, act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mockApi } from '../testUtils'
import Trace from './Trace'

const events = [
  { seq: 1, kind: 'status', content: { status: 'in_progress', model: 'm/x' }, created_at: '' },
  { seq: 2, kind: 'tool_call', content: { name: 'get_claim', args: {} }, created_at: '' },
  { seq: 3, kind: 'tool_result', content: { name: 'get_claim', content: 'Flight canceled' }, created_at: '' },
  { seq: 4, kind: 'decision', content: { decision: 'approve', payout: 420, rationale: 'Covered by section 3.' }, created_at: '' },
]

describe('Trace', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders events in order and stops polling when decided', async () => {
    const { fetchMock } = mockApi({ '/api/claims/CLM-1/trace': events })
    const { rerender } = render(<Trace claimId="CLM-1" status="in_progress" />)

    await screen.findByText(/Flight canceled/)
    const items = screen.getAllByRole('listitem').map((li) => li.textContent ?? '')
    expect(items[0]).toMatch(/status/i)
    expect(items[1]).toMatch(/get_claim/)
    expect(items[2]).toMatch(/Flight canceled/)
    expect(items[3]).toMatch(/approve/i)

    const before = fetchMock.mock.calls.length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100)
    })
    expect(fetchMock.mock.calls.length).toBeGreaterThan(before)

    rerender(<Trace claimId="CLM-1" status="decided" />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })
    const settled = fetchMock.mock.calls.length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(fetchMock.mock.calls.length).toBe(settled)
  })

  it('shows an empty state before the agent has started', async () => {
    mockApi({ '/api/claims/CLM-1/trace': [] })
    render(<Trace claimId="CLM-1" status="waiting" />)
    expect(await screen.findByText(/no activity yet/i)).toBeInTheDocument()
  })
})
