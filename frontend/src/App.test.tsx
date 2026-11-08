import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import App from './App'
import { MARCUS, mockApi, signedIn } from './testUtils'

const held = (id: string) => ({
  payment_id: `PAY-${id}`,
  claim_id: id,
  amount: 2000,
  status: 'held',
  customer_name: 'Maya Patel',
  claim_type: 'baggage',
  rationale: null,
})

describe('App navigation', () => {
  it('shows how many payments wait for approval next to the Approvals link', async () => {
    mockApi({ '/api/claims': [], '/api/dispatches/latest': { run: null, claims: [], totals: null, summary: null }, '/api/approvals': [held('CLM-1'), held('CLM-2'), held('CLM-3')] })
    render(
      <MemoryRouter initialEntries={['/']}>{signedIn(<App />)}</MemoryRouter>,
    )
    const link = await screen.findByRole('link', { name: /Approvals/ })
    expect(await screen.findByLabelText('3 waiting for approval')).toBeInTheDocument()
    expect(link).toHaveAttribute('href', '/approvals')
  })

  it("shows who is signed in, with their role and a way to sign out", async () => {
    mockApi({ '/api/claims': [], '/api/dispatches/latest': { run: null, claims: [], totals: null, summary: null }, '/api/approvals': [] })
    render(<MemoryRouter initialEntries={['/']}>{signedIn(<App />, MARCUS)}</MemoryRouter>)
    expect(await screen.findByText('Marcus Oyelaran')).toBeInTheDocument()
    expect(screen.getByText('Claims Handler')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
  })

  it('links to the scoreboard', async () => {
    mockApi({ '/api/claims': [], '/api/dispatches/latest': { run: null, claims: [], totals: null, summary: null }, '/api/approvals': [] })
    render(
      <MemoryRouter initialEntries={['/']}>{signedIn(<App />)}</MemoryRouter>,
    )
    expect(await screen.findByRole('link', { name: 'Scoreboard' })).toHaveAttribute('href', '/scoreboard')
  })

  it('still renders with a claim left for a handler', async () => {
    const stuck = { claim_id: 'CLM-9', claim_type: 'baggage', summary: 's', customer_name: 'Maya Patel',
      amount_claimed: 10, status: 'needs_handler', decision: null, payout: null, attempts: 2 }
    mockApi({ '/api/claims': [stuck], '/api/dispatches/latest': { run: null, claims: [], totals: null, summary: null }, '/api/approvals': [] })
    render(
      <MemoryRouter initialEntries={['/']}>{signedIn(<App />)}</MemoryRouter>,
    )
    expect(await screen.findByText('CLM-9')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Approvals' })).toBeInTheDocument()
  })

  it('shows no count when nothing waits', async () => {
    mockApi({ '/api/claims': [], '/api/dispatches/latest': { run: null, claims: [], totals: null, summary: null }, '/api/approvals': [] })
    render(
      <MemoryRouter initialEntries={['/']}>{signedIn(<App />)}</MemoryRouter>,
    )
    await screen.findByRole('link', { name: 'Approvals' })
    expect(screen.queryByLabelText(/waiting for approval/)).not.toBeInTheDocument()
  })
})
