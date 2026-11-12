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
  attempts: 0,
  ...extra,
})

const NO_RUN = { run: null, claims: [], totals: null, summary: null }
const dispatch = (status: string) => ({
  run: { run_id: 1, kind: 'dispatch', status, workers: 3, model: 'm', reasoning_effort: 'low', error: null, started_at: '2026-11-05T10:00:00+00:00', finished_at: null },
  claims: [{ claim_id: 'CLM-1', position: 1, status: 'in_progress', attempts: 0, decision: null, payout: null, payment: null, started_at: '2026-11-05T10:00:01+00:00', finished_at: null, cost_usd: null, seconds: 0 }],
  totals: { calls: 0, input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, cost_usd: null, seconds: 0 },
  summary: null,
})

describe('Queue', () => {
  it('shows each claim with a status badge for all four statuses', async () => {
    mockApi({
      '/api/dispatches/latest': NO_RUN,
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

  it('starts a dispatch with 3 claim agents at once to start with', async () => {
    const { calls } = mockApi({
      '/api/dispatches/latest': NO_RUN,
      '/api/claims': [claim('CLM-1', 'waiting')],
      'POST /api/dispatches': { status: 'started' },
    })
    renderAt(<Queue />, '/')
    expect(await screen.findByLabelText('Claim agents at once')).toHaveValue(3)
    await userEvent.click(screen.getByRole('button', { name: 'Start dispatch' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ workers: 3 }))
  })

  it('starts a dispatch with the N chosen', async () => {
    const { calls } = mockApi({
      '/api/dispatches/latest': NO_RUN,
      '/api/claims': [claim('CLM-1', 'waiting')],
      'POST /api/dispatches': { status: 'started' },
    })
    renderAt(<Queue />, '/')
    const field = await screen.findByLabelText('Claim agents at once')
    await userEvent.clear(field)
    await userEvent.type(field, '25')
    await userEvent.click(screen.getByRole('button', { name: 'Start dispatch' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ workers: 25 }))
  })

  it('offers only 1 to 25 claim agents at once', async () => {
    mockApi({ '/api/dispatches/latest': NO_RUN, '/api/claims': [claim('CLM-1', 'waiting')] })
    renderAt(<Queue />, '/')
    const field = await screen.findByLabelText('Claim agents at once')
    expect(field).toHaveAttribute('min', '1')
    expect(field).toHaveAttribute('max', '25')
    await userEvent.clear(field)
    await userEvent.type(field, '26')
    expect(screen.getByRole('button', { name: 'Start dispatch' })).toBeDisabled()
  })

  it('can carry on claims in progress, even with nothing waiting', async () => {
    const { calls } = mockApi({
      '/api/dispatches/latest': NO_RUN,
      '/api/claims': [claim('CLM-3', 'in_progress')],
      'POST /api/dispatches': { status: 'started' },
    })
    renderAt(<Queue />, '/')
    await userEvent.click(await screen.findByRole('button', { name: 'Start dispatch' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
  })

  it('does not offer a second dispatch while one runs', async () => {
    mockApi({ '/api/dispatches/latest': dispatch('running'), '/api/claims': [claim('CLM-1', 'in_progress'), claim('CLM-2', 'waiting')] })
    renderAt(<Queue />, '/')
    await screen.findByText('CLM-2')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start dispatch' })).toBeDisabled())
  })

  it('has nothing to offer when every claim is finished', async () => {
    mockApi({ '/api/dispatches/latest': NO_RUN, '/api/claims': [claim('CLM-3', 'decided')] })
    renderAt(<Queue />, '/')
    await screen.findByText('CLM-3')
    expect(screen.getByRole('button', { name: 'Start dispatch' })).toBeDisabled()
  })

  it('says so when the agent is not running', async () => {
    mockApi({
      '/api/dispatches/latest': NO_RUN,
      '/api/claims': [claim('CLM-1', 'waiting')],
      'POST /api/dispatches': { status: 503, body: { detail: 'the agent is not running' } },
    })
    renderAt(<Queue />, '/')
    await userEvent.click(await screen.findByRole('button', { name: 'Start dispatch' }))
    expect(await screen.findByText(/the agent is not running/i)).toBeInTheDocument()
  })

  it('says so when a dispatch or evaluation is already running', async () => {
    mockApi({
      '/api/dispatches/latest': NO_RUN,
      '/api/claims': [claim('CLM-1', 'waiting')],
      'POST /api/dispatches': { status: 409, body: { detail: 'a dispatch or evaluation is already running' } },
    })
    renderAt(<Queue />, '/')
    await userEvent.click(await screen.findByRole('button', { name: 'Start dispatch' }))
    expect(await screen.findByText('A dispatch or evaluation is already running.')).toBeInTheDocument()
  })

  it('shows the latest dispatch in the run panel', async () => {
    mockApi({ '/api/dispatches/latest': dispatch('running'), '/api/claims': [claim('CLM-1', 'in_progress')] })
    renderAt(<Queue />, '/')
    expect(await screen.findByText(/Dispatch 1/)).toBeInTheDocument()
  })

  it('has no tick boxes and no limit: a dispatch works every claim', async () => {
    mockApi({ '/api/dispatches/latest': NO_RUN, '/api/claims': [claim('CLM-1', 'waiting'), claim('CLM-2', 'waiting')] })
    renderAt(<Queue />, '/')
    await screen.findByText('CLM-1')
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
    expect(screen.queryByLabelText('Limit')).not.toBeInTheDocument()
  })

  it('counts claims left for a handler and notes a failed attempt', async () => {
    mockApi({
      '/api/dispatches/latest': NO_RUN,
      '/api/claims': [claim('CLM-1', 'needs_handler', { attempts: 2 }), claim('CLM-2', 'waiting', { attempts: 1 })],
    })
    renderAt(<Queue />, '/')
    await screen.findByText('CLM-1')
    const count = within(screen.getByRole('group', { name: 'Needs a handler' })).getByText(/^\d+$/).textContent
    expect(count).toBe('1')
    expect(screen.getAllByText('Needs a handler').length).toBeGreaterThan(1)
    expect(screen.getByText(/1 failed attempt, back in the queue/)).toBeInTheDocument()
  })

  it('shows an error when the API fails', async () => {
    mockApi({ '/api/dispatches/latest': NO_RUN, '/api/claims': { status: 500, body: { detail: 'boom' } } })
    renderAt(<Queue />, '/')
    expect(await screen.findByText(/could not load/i)).toBeInTheDocument()
  })

  it('shows a sent-back claim with its status, and counts it', async () => {
    mockApi({ '/api/dispatches/latest': NO_RUN, '/api/claims': [claim('CLM-1', 'sent_back'), claim('CLM-2', 'sent_back')] })
    renderAt(<Queue />, '/')
    await screen.findByText('CLM-1')
    expect(within(screen.getByRole('group', { name: 'Sent back' })).getByText(/^\d+$/).textContent).toBe('2')
    expect(screen.getAllByText('Sent back', { selector: '.badge' })).toHaveLength(2)
  })

  it('summarizes the queue in one tile per status', async () => {
    mockApi({
      '/api/dispatches/latest': NO_RUN,
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
      '/api/dispatches/latest': NO_RUN,
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
