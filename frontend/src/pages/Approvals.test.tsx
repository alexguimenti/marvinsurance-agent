import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { mockApi, renderAt } from '../testUtils'
import Approvals from './Approvals'

const held = {
  payment_id: 'PAY-CLM-1003',
  claim_id: 'CLM-1003',
  amount: 3750,
  status: 'held',
  customer_name: 'Tom Becker',
  claim_type: 'emergency_medical',
  rationale: 'Covered by section 7.',
}
const handlers = [
  { handler_id: 'HND-01', name: 'Dana Whitfield', role: 'Senior Claims Handler' },
  { handler_id: 'HND-02', name: 'Marcus Oyelaran', role: 'Claims Handler' },
]

describe('Approvals', () => {
  it('lists held payments and approving calls approvePayment with the chosen handler', async () => {
    let approved = false
    const { calls } = mockApi({
      '/api/approvals': () => (approved ? [] : [held]),
      '/api/handlers': handlers,
      'POST /api/payments/PAY-CLM-1003/approve': () => {
        approved = true
        return { payment_id: 'PAY-CLM-1003', status: 'paid' }
      },
    })
    renderAt(<Approvals />, '/approvals')

    expect(await screen.findByText('CLM-1003')).toBeInTheDocument()
    expect(screen.getByText('$3,750.00')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText(/approve as/i), 'HND-02')
    await userEvent.click(screen.getByRole('button', { name: /approve payment/i }))

    await waitFor(() =>
      expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ handler_id: 'HND-02' }),
    )
    expect(await screen.findByText(/nothing is waiting for approval/i)).toBeInTheDocument()
  })

  it('shows a clear error when the API returns 403', async () => {
    mockApi({
      '/api/approvals': [held],
      '/api/handlers': handlers,
      'POST /api/payments/PAY-CLM-1003/approve': { status: 403, body: { detail: 'HND-01 cannot approve' } },
    })
    renderAt(<Approvals />, '/approvals')
    await userEvent.click(await screen.findByRole('button', { name: /approve payment/i }))
    expect(await screen.findByText(/not allowed to approve/i)).toBeInTheDocument()
    expect(screen.getByText('CLM-1003')).toBeInTheDocument()
  })
})
