import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { mockApi, renderAt } from '../testUtils'
import Queue from './Queue'

const claim = (id: string, status: string, extra = {}) => ({
  claim_id: id,
  claim_type: 'baggage',
  summary: `Summary of ${id}`,
  customer_name: 'Maya Patel',
  amount_claimed: 420,
  status,
  decision: null,
  payout: null,
  ...extra,
})

describe('Queue', () => {
  it('shows each claim with a status badge for all four statuses', async () => {
    mockApi({
      '/api/claims': [
        claim('CLM-1', 'waiting'),
        claim('CLM-2', 'in_progress'),
        claim('CLM-3', 'awaiting_approval', { decision: 'approve', payout: 2000 }),
        claim('CLM-4', 'decided', { decision: 'decline', payout: 0 }),
      ],
    })
    renderAt(<Queue />, '/')
    expect(await screen.findByText('CLM-1')).toBeInTheDocument()
    for (const label of ['Waiting', 'In progress', 'Awaiting approval', 'Decided']) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0)
    }
    expect(screen.getByText('$2,000.00')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'CLM-1' })).toHaveAttribute('href', '/claims/CLM-1')
  })

  it('"Run queue" button calls runQueue', async () => {
    const { calls } = mockApi({
      '/api/claims': [claim('CLM-1', 'waiting')],
      'POST /api/queue/run': { queued: 1 },
    })
    renderAt(<Queue />, '/')
    await userEvent.click(await screen.findByRole('button', { name: /run queue/i }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/queue/run')).toBe(true))
  })

  it('shows an error when the API fails', async () => {
    mockApi({ '/api/claims': { status: 500, body: { detail: 'boom' } } })
    renderAt(<Queue />, '/')
    expect(await screen.findByText(/could not load/i)).toBeInTheDocument()
  })
})
