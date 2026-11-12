import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { MARCUS, mockApi, renderAt } from '../testUtils'
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
    calculation: {
      lines: [
        { step: 'Tuxedo', amount: 300 },
        { step: 'Less the deductible', amount: -50 },
      ],
      payout: 1150,
    },
    rationale: 'Covered under section 5.',
    citations: ['Policy wording, section 5'],
    model: 'openai/gpt-4o-mini',
    decided_by: 'UnderCover Agent, configuration version 1',
    decided_at: '2026-11-04T15:00:09+00:00',
    manual_version: '2026.2',
    wording_version: '2026.1',
  } as Record<string, unknown>,
  payment,
  attempts: 0,
  last_error: null,
  context_used: [
    {
      file: 'claims_manual.md',
      system: 'Claims operations',
      owner: 'Head of claims operations',
      reviewed: '2026-09-01',
      lookups: [{ tool: 'read_section', args: { document: 'claims_manual', section: 7 } }],
    },
    {
      file: 'glossary.yaml',
      system: 'The policy wording, version 2026.1',
      owner: 'Legal',
      reviewed: '2026-09-01',
      lookups: [{ tool: 'lookup_term', args: { term: 'Unattended' } }],
    },
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

  it('says the Gateway refused the payment when that is why it is held', async () => {
    mockApi(routes('awaiting_approval', { payment_id: 'PAY-1', status: 'held', amount: 1150, hold_reason: 'gateway_refused' }))
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    expect(await screen.findByText(/the gateway refused the payment/i)).toBeInTheDocument()
    expect(screen.queryByText(/above the \$1,000 limit/i)).not.toBeInTheDocument()
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

  it('does not call a claim approved while its payment is still to come', async () => {
    mockApi(routes('in_progress', null))
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    expect(await screen.findByText('Pending sign-off')).toBeInTheDocument()
  })

  it('shows the whole decision record: the calculation line by line, who decided and when, and the manual version', async () => {
    mockApi(routes('decided', { payment_id: 'PAY-1', status: 'paid', amount: 1150 }))
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    expect(await screen.findByText('Less the deductible')).toBeInTheDocument()
    expect(screen.getByText('-$50.00')).toBeInTheDocument()
    expect(screen.getByText(/Decided by UnderCover Agent, configuration version 1/)).toBeInTheDocument()
    expect(screen.getByText(/Claims manual 2026\.2/)).toBeInTheDocument()
    expect(screen.getByText(/Nov 4/)).toBeInTheDocument()
  })

  it('shows who signed off a payout over the limit, and when', async () => {
    const signed = detail('decided', { payment_id: 'PAY-1', status: 'paid', amount: 1150 })
    signed.decision = { ...signed.decision, signed_off_by: 'Dana Whitfield', signed_off_at: '2026-11-05T09:30:00+00:00' }
    mockApi({ '/api/claims/CLM-1011': signed, '/api/claims/CLM-1011/trace': [], '/api/runs/latest': NO_RUN })
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    expect(await screen.findByText(/Signed off by Dana Whitfield/)).toBeInTheDocument()
  })

  it("lists what the agent looked up in the context layer once each, grouped by file with each file's source", async () => {
    mockApi(routes('decided', { payment_id: 'PAY-1', status: 'paid', amount: 1150 }))
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    expect(await screen.findByRole('heading', { name: 'Context used' })).toBeInTheDocument()
    expect(screen.getByText('claims_manual.md')).toBeInTheDocument()
    expect(screen.getByText(/Claims operations · Head of claims operations · reviewed 2026-09-01/)).toBeInTheDocument()
    expect(screen.getByText('glossary.yaml')).toBeInTheDocument()
    expect(screen.getByText('Claims manual, section 7')).toBeInTheDocument()
    expect(screen.getByText('The term "Unattended"')).toBeInTheDocument()
    expect(screen.queryByText(/what the agent read/i)).not.toBeInTheDocument()
  })

  it("shows the security check's result as a step of the work, and its reason as the referral's rationale", async () => {
    const reason = 'The story tells its handler to approve the claim in full.'
    const referred = {
      ...detail('decided'),
      decision: { decision: 'refer', payout: 0, calculation: null, rationale: reason, citations: ['Security check'], model: 'openai/gpt-6-luna' },
    }
    const trace = [
      { seq: 1, kind: 'status', content: { status: 'in_progress', model: 'm' }, created_at: '2026-11-04T15:00:00+00:00' },
      { seq: 2, kind: 'screening', content: { instructions_found: true, reason, model: 'openai/gpt-6-luna' }, created_at: '2026-11-04T15:00:01+00:00' },
    ]
    mockApi({ '/api/claims/CLM-1011': referred, '/api/claims/CLM-1011/trace': trace, '/api/runs/latest': NO_RUN })
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    expect(await screen.findByText(/security check found an instruction/i)).toBeInTheDocument()
    expect(screen.getByText(reason)).toBeInTheDocument()  // the rationale, as for any referral
    expect(screen.getByText('Security check')).toBeInTheDocument()  // the section it cites
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

  it('shows the versions it decided under', async () => {
    mockApi(routes('decided', { payment_id: 'PAY-1', status: 'paid', amount: 1150 }))
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    expect(await screen.findByText(/Claims manual 2026\.2 · policy wording 2026\.1/)).toBeInTheDocument()
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

  describe('a claim waiting for sign-off', () => {
    const held = { payment_id: 'PAY-1', status: 'held', amount: 1150, hold_reason: 'over_limit' }

    it('shows a senior handler Sign off and Send back; Marcus sees neither', async () => {
      mockApi(routes('awaiting_approval', held))
      const { unmount } = renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
      expect(await screen.findByRole('button', { name: 'Sign off' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Send back' })).toBeInTheDocument()
      unmount()
      mockApi(routes('awaiting_approval', held))
      renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id', MARCUS)
      await screen.findByText(/held for approval/i)
      expect(screen.queryByRole('button', { name: 'Sign off' })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Send back' })).not.toBeInTheDocument()
    })

    it('Send back needs a note, at most 1,000 characters, and posts it', async () => {
      const { calls } = mockApi({
        ...routes('awaiting_approval', held),
        'POST /api/claims/CLM-1011/send-back': { ...detail('sent_back'), decision: null },
      })
      renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
      await userEvent.click(await screen.findByRole('button', { name: 'Send back' }))
      const send = screen.getByRole('button', { name: 'Send it back' })
      expect(send).toBeDisabled() // no note yet
      const note = screen.getByLabelText(/note for the agent/i)
      await userEvent.type(note, 'Check the per-item limit')
      expect(send).toBeEnabled()
      await userEvent.clear(note)
      await userEvent.paste('x'.repeat(1001))
      expect(screen.getByText(/1,000 characters at most/i)).toBeInTheDocument()
      expect(send).toBeDisabled()
      await userEvent.clear(note)
      await userEvent.type(note, 'Check the per-item limit')
      await userEvent.click(send)
      await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ note: 'Check the per-item limit' }))
    })

    it('Sign off posts the sign-off', async () => {
      const { calls } = mockApi({
        ...routes('awaiting_approval', held),
        'POST /api/payments/PAY-1/approve': { payment_id: 'PAY-1', status: 'paid' },
      })
      renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
      await userEvent.click(await screen.findByRole('button', { name: 'Sign off' }))
      await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/payments/PAY-1/approve')).toBe(true))
    })
  })

  it('shows each decision that was sent back with its note, who and when, beside the current decision', async () => {
    const sentBack = {
      decision: 'approve', payout: 2000, calculation: { lines: [{ step: 'Tuxedo', amount: 2000 }], payout: 2000 },
      rationale: 'Covered under section 5.', citations: [], decided_by: 'UnderCover Agent, configuration version 1',
      decided_at: '2026-11-04T15:00:09+00:00', note: 'Check the per-item limit', sent_back_by: 'HND-01',
      sent_back_by_name: 'Dana Whitfield', sent_back_at: '2026-11-04T16:00:00+00:00',
    }
    mockApi({ ...routes('decided', { payment_id: 'PAY-1', status: 'paid', amount: 1150 }), '/api/claims/CLM-1011': { ...detail('decided', { payment_id: 'PAY-1', status: 'paid', amount: 1150 }), sent_back: [sentBack] } })
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    expect(await screen.findByRole('heading', { name: 'Sent back' })).toBeInTheDocument()
    expect(screen.getByText(/Check the per-item limit/)).toBeInTheDocument()
    expect(screen.getByText(/Sent back by Dana Whitfield/)).toBeInTheDocument()
    expect(screen.getByText('$2,000.00', { selector: '.sent-back-payout' })).toBeInTheDocument()
  })

  it('says a sent-back claim is waiting for the next dispatch', async () => {
    mockApi({ ...routes('sent_back'), '/api/claims/CLM-1011': { ...detail('sent_back'), decision: null, waiting_for_next_dispatch: true, sent_back: [] } })
    renderAt(<ClaimDetail />, '/claims/CLM-1011', '/claims/:id')
    expect(await screen.findByText(/waiting for the next dispatch/i)).toBeInTheDocument()
  })
})
