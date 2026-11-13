import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import ConfirmationCard from './ConfirmationCard'
import { CARD } from './fixtures'

describe('the confirmation card', () => {
  it('shows the claim, the customer, the amount and the reason, with two buttons', async () => {
    const onAnswer = vi.fn()
    render(<MemoryRouter><ConfirmationCard card={CARD} onAnswer={onAnswer} busy={false} /></MemoryRouter>)
    expect(screen.getByRole('link', { name: 'CLM-1003' })).toHaveAttribute('href', '/claims/CLM-1003')
    expect(screen.getByText('Maya Patel')).toBeInTheDocument()
    expect(screen.getByText('$3,750.00')).toBeInTheDocument()
    expect(screen.getByText('Covered under section 10.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Sign off and pay' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onAnswer.mock.calls).toEqual([['sign_off'], ['cancel']])
  })

  it('cannot be pressed while an answer is on its way', () => {
    render(<MemoryRouter><ConfirmationCard card={CARD} onAnswer={vi.fn()} busy /></MemoryRouter>)
    expect(screen.getByRole('button', { name: 'Sign off and pay' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
  })
})
