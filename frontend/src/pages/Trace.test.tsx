import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

describe('Trace formatting', () => {
  const claimJson = JSON.stringify({
    claim_id: 'CLM-1001',
    claim_type: 'trip_delay',
    amount_claimed: 420,
    items: [{ description: 'Airport hotel', amount: 245 }],
  })
  const traced = [
    { seq: 1, kind: 'tool_call', content: { name: 'get_claim', args: {} }, created_at: '' },
    {
      seq: 2,
      kind: 'tool_result',
      content: { name: 'get_claim', content: `<claim_data>\n${claimJson}\n</claim_data>` },
      created_at: '',
    },
    {
      seq: 3,
      kind: 'tool_result',
      content: { name: 'get_policy_wording', content: '[policy wording, 9000 characters]' },
      created_at: '',
    },
  ]

  it('collapses and expands the whole panel', async () => {
    mockApi({ '/api/claims/CLM-1/trace': traced })
    render(<Trace claimId="CLM-1" status="decided" />)
    await screen.findAllByRole('listitem')

    await userEvent.click(screen.getByRole('button', { name: 'Collapse activity' }))
    expect(screen.queryAllByRole('listitem')).toHaveLength(0)
    const expand = screen.getByRole('button', { name: 'Expand activity' })
    expect(expand).toHaveAttribute('aria-expanded', 'false')

    await userEvent.click(expand)
    expect(screen.getAllByRole('listitem')).toHaveLength(3)
  })

  it('summarizes a tool result and shows it formatted on demand', async () => {
    mockApi({ '/api/claims/CLM-1/trace': traced })
    render(<Trace claimId="CLM-1" status="decided" />)

    expect(await screen.findByText(/CLM-1001 · trip delay · \$420\.00 claimed/)).toBeInTheDocument()
    expect(screen.queryByText(/claim_data/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Airport hotel/)).not.toBeInTheDocument()

    await userEvent.click(screen.getAllByRole('button', { name: 'Show details' })[0])
    const pre = document.querySelector('pre.json')
    expect(pre?.textContent).toContain('"claim_id": "CLM-1001"')
    expect(pre?.textContent).toContain('\n  "claim_type"')
    expect(screen.getByText(/Airport hotel/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Hide details' }))
    expect(document.querySelector('pre.json')).toBeNull()
  })

  it('summarizes the policy wording without dumping it', async () => {
    mockApi({ '/api/claims/CLM-1/trace': traced })
    render(<Trace claimId="CLM-1" status="decided" />)
    expect(await screen.findByText(/policy wording read/i)).toBeInTheDocument()
  })

  it('shows what the analysis cost at the end of the log', async () => {
    mockApi({
      '/api/claims/CLM-1/trace': [
        ...traced,
        {
          seq: 4,
          kind: 'usage',
          content: { calls: 9, input_tokens: 14200, output_tokens: 1900, reasoning_tokens: 1200, cost_usd: 0.0031 },
          created_at: '',
        },
      ],
    })
    render(<Trace claimId="CLM-1" status="decided" />)
    expect(await screen.findByText(/\$0\.0031/)).toBeInTheDocument()
    expect(screen.getByText(/14,200 tokens in/)).toBeInTheDocument()
    expect(screen.getByText(/1,900 out/)).toBeInTheDocument()
    expect(screen.getByText(/9 model calls/)).toBeInTheDocument()
  })

  it('says so when the provider reports no cost', async () => {
    mockApi({
      '/api/claims/CLM-1/trace': [
        { seq: 1, kind: 'usage', content: { calls: 1, input_tokens: 5, output_tokens: 5, reasoning_tokens: 0, cost_usd: null }, created_at: '' },
      ],
    })
    render(<Trace claimId="CLM-1" status="decided" />)
    expect(await screen.findByText(/cost not reported/i)).toBeInTheDocument()
  })

  it('shows a retry in the log with the reason and the attempt', async () => {
    mockApi({
      '/api/claims/CLM-1/trace': [
        {
          seq: 1,
          kind: 'retry',
          content: { message: 'Attempt 1 of 3 failed (ValueError: the agent finished without a decision). Trying again.' },
          created_at: '',
        },
      ],
    })
    render(<Trace claimId="CLM-1" status="decided" />)
    expect(await screen.findByText(/Attempt 1 of 3 failed/)).toBeInTheDocument()
    expect(screen.getByText('retry')).toBeInTheDocument()
  })
})
