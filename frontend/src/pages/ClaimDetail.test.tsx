import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { mockApi, renderAt } from '../testUtils'
import ClaimDetail from './ClaimDetail'

const detail = (status: string, payment: unknown = null) => ({
  status,
  claim: {
    claim_id: 'CLM-1011',
    claim_type: 'baggage',
    story: 'My suitcase was lost on the way.',
    amount_claimed: 2400,
    items: [{ description: 'Tuxedo', category: 'clothing', amount: 450 }],
    documents: [{ name: 'Carrier report', type: 'carrier_report', content: 'Bag lost by carrier.' }],
  },
  policy: { policy_id: 'POL-1', plan: 'premium', add_ons: [], travelers: ['Maya Patel'] },
  customer: { name: 'Maya Patel', previous_claims: [] },
  decision: {
    decision: 'approve',
    payout: 1150,
    calculation: [
      { step: 'Tuxedo', amount: 300 },
      { step: 'Less the deductible', amount: -50 },
    ],
    rationale: 'Covered under section 5.',
    citations: ['Policy wording, section 5'],
    model: 'openai/gpt-4o-mini',
    manual_version: '2026.2',
    wording_version: '2026.1',
  },
  payment,
  attempts: 0,
  last_error: null,
  read: [
    { document: 'claims_manual', section: 7, title: 'Interpretation notes' },
    { document: 'policy_wording', section: 5, title: 'Baggage and Personal Effects' },
  ],
})

const NO_RUN = { run: null, todo: [] }

function routes(status: string, payment: unknown = null) {
  return { '/api/claims/CLM-1011': detail(status, payment), '/api/claims/CLM-1011/trace': [], '/api/runs/latest': NO_RUN }
}

describe('ClaimDetail', () => {
  it("shows an evaluation's copy as such, with nothing to sign off", async () => {
    const copy = { ...detail('awaiting_approval', { payment_id: 'PAY-E7-CLM-1011', status: 'held', amount: 2400 }), copy_of: 'CLM-1011', evaluation_id: 7 }
    mockApi({ '/api/claims/CLM-1011': copy, '/api/claims/CLM-1011/trace': [] })
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    expect(await screen.findByText(/Evaluation copy of/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'evaluation 7' })).toHaveAttribute('href', '/scoreboard/7')
    expect(screen.queryByText(/held for approval/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /go to approvals/i })).not.toBeInTheDocument()
  })

  it('shows story, items, decision, payout, calculation steps and rationale', async () => {
    mockApi(routes('decided', { payment_id: 'PAY-1', status: 'paid', amount: 1150 }))
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    expect(await screen.findByText('My suitcase was lost on the way.')).toBeInTheDocument()
    // once in the claimed items, once in the calculation steps
    expect(screen.getAllByText('Tuxedo', { selector: 'td' })).toHaveLength(2)
    expect(screen.getByText('Approve')).toBeInTheDocument()
    expect(screen.getAllByText('$1,150.00').length).toBeGreaterThan(0)
    expect(screen.getByText('Less the deductible')).toBeInTheDocument()
    expect(screen.getByText('Covered under section 5.')).toBeInTheDocument()
    expect(screen.getByText('Policy wording, section 5')).toBeInTheDocument()
    expect(screen.getByText('Carrier report')).toBeInTheDocument()
  })

  it('shows a "Held for approval" notice when status is awaiting_approval', async () => {
    mockApi(routes('awaiting_approval', { payment_id: 'PAY-1', status: 'held', amount: 1150 }))
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    expect(await screen.findByText(/held for approval/i)).toBeInTheDocument()
  })

  it('shows who signed off and when on a paid claim', async () => {
    mockApi(routes('decided', {
      payment_id: 'PAY-1', status: 'paid', amount: 1150, approved_by: 'HND-01', approved_by_name: 'Dana Whitfield',
      approved_at: '2026-11-04T15:30:00+00:00', paid_at: '2026-11-04T15:30:02+00:00',
    }))
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    const line = await screen.findByText(/signed off by dana whitfield/i)
    expect(line).toHaveTextContent('$1,150.00')
    expect(line).toHaveTextContent('2026')
    expect(screen.getByText('Approve')).toBeInTheDocument()
  })

  it('shows in progress, with no held notice, between the sign-off and the payment', async () => {
    mockApi(routes('in_progress', {
      payment_id: 'PAY-1', status: 'signed_off', amount: 1150, approved_by: 'HND-01', approved_by_name: 'Dana Whitfield',
      approved_at: '2026-11-04T15:30:00+00:00', paid_at: null,
    }))
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    expect(await screen.findByText(/signed off by dana whitfield.*the agent is paying/i)).toBeInTheDocument()
    expect(screen.queryByText(/held for approval/i)).not.toBeInTheDocument()
    expect(screen.getByText('Pending sign-off')).toBeInTheDocument()
  })

  it('does not call a claim approved while its payment is still to come', async () => {
    mockApi(routes('in_progress', null))
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    expect(await screen.findByText('Pending sign-off')).toBeInTheDocument()
  })

  it('offers no way to run one claim: a dispatch works every claim', async () => {
    mockApi({ '/api/claims/CLM-1011': { ...detail('waiting'), decision: null }, '/api/claims/CLM-1011/trace': [] })
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    await screen.findByText('CLM-1011')
    expect(screen.queryByRole('button', { name: /run/i })).not.toBeInTheDocument()
  })

  it('does not ask for the latest run', async () => {
    const { calls } = mockApi(routes('decided', { payment_id: 'PAY-1', status: 'paid', amount: 1150 }))
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    await screen.findByText('Covered under section 5.')
    expect(calls.some((c) => c.url === '/api/dispatches/latest')).toBe(false)
  })

  it('lists what the agent read, and the versions it decided under', async () => {
    mockApi(routes('decided', { payment_id: 'PAY-1', status: 'paid', amount: 1150 }))
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    expect(await screen.findByRole('heading', { name: 'What the agent read' })).toBeInTheDocument()
    expect(screen.getByText('Claims manual, section 7: Interpretation notes')).toBeInTheDocument()
    expect(screen.getByText('Policy wording, section 5: Baggage and Personal Effects')).toBeInTheDocument()
    expect(screen.getByText(/Claims manual 2026\.2 · policy wording 2026\.1/)).toBeInTheDocument()
  })

  it('explains a claim left for a handler', async () => {
    const stuck = { ...detail('needs_handler'), decision: null, attempts: 2, last_error: 'RuntimeError: provider down' }
    mockApi({ '/api/claims/CLM-1011': stuck, '/api/claims/CLM-1011/trace': [], '/api/runs/latest': NO_RUN })
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    expect(await screen.findByText(/failed 2 times/)).toBeInTheDocument()
    expect(screen.getByText(/RuntimeError: provider down/)).toBeInTheDocument()
  })

  it('shows the cost of the analysis next to the decision', async () => {
    const withUsage = {
      ...detail('decided'),
      usage: { calls: 9, input_tokens: 14200, output_tokens: 1900, reasoning_tokens: 0, cost_usd: 0.0031 },
    }
    mockApi({ '/api/claims/CLM-1011': withUsage, '/api/claims/CLM-1011/trace': [], '/api/runs/latest': NO_RUN })
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    expect(await screen.findByText(/Analysis cost \$0\.0031/)).toBeInTheDocument()
  })

  it('shows no cost line when none was reported', async () => {
    mockApi({ '/api/claims/CLM-1011': { ...detail('decided'), usage: null }, '/api/claims/CLM-1011/trace': [], '/api/runs/latest': NO_RUN })
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    await screen.findByText('Covered under section 5.')
    expect(screen.queryByText(/Analysis cost/)).not.toBeInTheDocument()
  })

  it('shows "Pending sign-off", not an Approve stamp, while the payment is held', async () => {
    mockApi(routes('awaiting_approval', { payment_id: 'PAY-1', status: 'held', amount: 1150 }))
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    await screen.findByText(/held for approval/i)
    expect(screen.getByText('Pending sign-off')).toBeInTheDocument()
    expect(screen.queryByText('Approve')).not.toBeInTheDocument()
    expect(document.querySelector('.decision-approve')).toBeNull()
  })

  it('stamps Approve once the claim is decided', async () => {
    mockApi(routes('decided', { payment_id: 'PAY-1', status: 'paid', amount: 1150 }))
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    expect(await screen.findByText('Approve')).toBeInTheDocument()
    expect(screen.queryByText('Pending sign-off')).not.toBeInTheDocument()
  })
})
