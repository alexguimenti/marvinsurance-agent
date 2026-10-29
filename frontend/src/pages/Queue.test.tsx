import { screen, waitFor, within } from '@testing-library/react'
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

  it('lets you tick waiting claims and run only those', async () => {
    const { calls } = mockApi({
      '/api/claims': [claim('CLM-1', 'waiting'), claim('CLM-2', 'waiting'), claim('CLM-3', 'decided')],
      'POST /api/queue/run': { queued: 1 },
    })
    renderAt(<Queue />, '/')
    await screen.findByText('CLM-1')
    expect(screen.getAllByRole('checkbox')).toHaveLength(3) // select all + 2 waiting rows
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select CLM-2' }))
    await userEvent.click(screen.getByRole('button', { name: 'Run selected (1)' }))
    await waitFor(() =>
      expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ claim_ids: ['CLM-2'] }),
    )
  })

  it('select all ticks every waiting claim', async () => {
    mockApi({ '/api/claims': [claim('CLM-1', 'waiting'), claim('CLM-2', 'waiting'), claim('CLM-3', 'decided')] })
    renderAt(<Queue />, '/')
    await screen.findByText('CLM-1')
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select all waiting claims' }))
    expect(screen.getByRole('button', { name: 'Run selected (2)' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select all waiting claims' }))
    expect(screen.getByRole('button', { name: 'Run queue' })).toBeInTheDocument()
  })

  it('summarizes the queue in one tile per status', async () => {
    mockApi({
      '/api/claims': [
        claim('CLM-1', 'waiting'),
        claim('CLM-2', 'waiting'),
        claim('CLM-3', 'in_progress'),
        claim('CLM-4', 'awaiting_approval', { decision: 'approve', payout: 2000 }),
        claim('CLM-5', 'decided', { decision: 'refer', payout: 0 }),
      ],
    })
    renderAt(<Queue />, '/')
    await screen.findByText('CLM-1')
    const count = (name: string) => within(screen.getByRole('group', { name })).getByText(/^\d+$/).textContent
    expect(count('Waiting')).toBe('2')
    expect(count('In progress')).toBe('1')
    expect(count('Awaiting approval')).toBe('1')
    expect(count('Decided')).toBe('1')
  })

  it('does not stamp APPROVE on a claim that still waits for a handler', async () => {
    mockApi({
      '/api/claims': [
        claim('CLM-1', 'awaiting_approval', { decision: 'approve', payout: 2000 }),
        claim('CLM-2', 'decided', { decision: 'approve', payout: 300 }),
      ],
    })
    renderAt(<Queue />, '/')
    await screen.findByText('CLM-1')
    const held = within(screen.getByText('CLM-1').closest('tr')!)
    expect(held.queryByText('Approve')).not.toBeInTheDocument()
    expect(held.getByText('Pending sign-off')).toBeInTheDocument()
    const paid = within(screen.getByText('CLM-2').closest('tr')!)
    expect(paid.getByText('Approve')).toBeInTheDocument()
    expect(paid.queryByText('Pending sign-off')).not.toBeInTheDocument()
  })
})
