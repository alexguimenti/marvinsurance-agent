import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { MARCUS, mockApi, renderAt } from '../testUtils'
import Approvals from './Approvals'

const held = {
  payment_id: 'PAY-CLM-1003',
  claim_id: 'CLM-1003',
  amount: 3750,
  status: 'held',
  customer_name: 'Tom Becker',
  claim_type: 'emergency_medical',
  rationale: 'Covered by section 7.',
  hold_reason: 'over_limit',
}

describe('Approvals', () => {
  it('lists held payments, and approving signs off as the signed-in handler with no name to choose', async () => {
    let approved = false
    const { calls } = mockApi({
      '/api/approvals': () => (approved ? [] : [held]),
      'POST /api/payments/PAY-CLM-1003/approve': () => {
        approved = true
        return { payment_id: 'PAY-CLM-1003', status: 'paid' }
      },
    })
    renderAt(<Approvals />, '/approvals')

    expect(await screen.findByText('CLM-1003')).toBeInTheDocument()
    expect(screen.getByText('$3,750.00')).toBeInTheDocument()
    expect(screen.queryByLabelText(/approve as/i)).not.toBeInTheDocument()
    expect(screen.getByText(/signing off as Dana Whitfield/i)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /approve payment/i }))

    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/payments/PAY-CLM-1003/approve')).toBe(true))
    expect(calls.find((c) => c.method === 'POST')?.body).toBeUndefined()
    expect(await screen.findByText(/nothing is waiting for approval/i)).toBeInTheDocument()
  })

  it('shows a clear error when the API returns 403', async () => {
    mockApi({
      '/api/approvals': [held],
      'POST /api/payments/PAY-CLM-1003/approve': { status: 403, body: { detail: 'HND-01 cannot approve' } },
    })
    renderAt(<Approvals />, '/approvals')
    await userEvent.click(await screen.findByRole('button', { name: /approve payment/i }))
    expect(await screen.findByText(/not allowed to approve/i)).toBeInTheDocument()
    expect(screen.getByText('CLM-1003')).toBeInTheDocument()
  })

  it("tells a handler who can't sign off over the limit what they can't do and why", async () => {
    mockApi({ '/api/approvals': [held] })
    renderAt(<Approvals />, '/approvals', '*', MARCUS)
    expect(await screen.findByText('CLM-1003')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /approve payment/i })).not.toBeInTheDocument()
    expect(screen.getByText(/can't sign off payouts over the limit/i)).toBeInTheDocument()
  })

  it('says why a payment waits: over the approval limit, or refused by the Gateway', async () => {
    mockApi({ '/api/approvals': [held, { ...held, payment_id: 'PAY-CLM-1004', claim_id: 'CLM-1004', hold_reason: 'gateway_refused' }] })
    renderAt(<Approvals />, '/approvals')
    expect(await screen.findByText('CLM-1004')).toBeInTheDocument()
    expect(screen.getByText('Over the approval limit')).toBeInTheDocument()
    expect(screen.getByText('The Gateway refused this payment')).toBeInTheDocument()
  })
})
