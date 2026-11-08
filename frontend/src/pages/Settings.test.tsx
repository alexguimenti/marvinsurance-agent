import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { MARCUS, mockApi, renderAt } from '../testUtils'
import Settings from './Settings'

const row = (name: string, value: unknown, by: string | null = null, byName: string | null = null) => ({
  name, value, changed_by: by ?? 'seed', changed_by_name: byName, changed_at: '2026-11-05T22:04:00+00:00',
})

const SETTINGS = {
  settings: [
    row('approval_limit', 1000),
    row('objectives', { accuracy: 0.6, grade: 1.0 }),
    row('noise', { accuracy: 0.1, grade: 0.15, cost_per_claim: 0.15, seconds_per_claim: 0.15 }),
    row('tolerance', { accuracy: 0.1, grade: 0.15, cost_per_claim: 1.0, seconds_per_claim: 0.25 }),
    row('round_spending_limit', 0.5, 'HND-01', 'Dana Whitfield'),
    row('agents_at_once', 25),
  ],
  models_allowed: {
    'openai/gpt-6-luna': { reasoning_efforts: ['none', 'low', 'medium'], price_in: 0.1, price_out: 0.5 },
    'deepseek/deepseek-v4.1-flash': { reasoning_efforts: ['low'], price_in: 0.3, price_out: 1.2 },
  },
}

afterEach(() => {
  localStorage.clear()
  delete document.documentElement.dataset.theme
})

describe('Settings', () => {
  it('shows each setting with who changed it and when', async () => {
    mockApi({ '/api/settings': SETTINGS })
    renderAt(<Settings />, '/settings')
    expect(await screen.findByLabelText('Approval limit (USD)')).toHaveValue(1000)
    expect(screen.getByLabelText('Spending limit for each round (USD)')).toHaveValue(0.5)
    expect(screen.getByLabelText('Claim agents at once')).toHaveValue(25)
    expect(screen.getByText(/Last changed by Dana Whitfield/)).toBeInTheDocument()
    expect(screen.getAllByText(/Starting value/).length).toBeGreaterThan(0)
  })

  it('asks for confirmation before a change, and sends nothing until it is given', async () => {
    const { calls } = mockApi({ '/api/settings': SETTINGS, 'PUT /api/settings/approval_limit': { name: 'approval_limit', value: 1500 } })
    renderAt(<Settings />, '/settings')
    const field = await screen.findByLabelText('Approval limit (USD)')
    await userEvent.clear(field)
    await userEvent.type(field, '1500')
    await userEvent.click(screen.getByRole('button', { name: 'Save approval limit' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/\$1,000\.00 to \$1,500\.00/)).toBeInTheDocument()
    expect(calls.some((c) => c.method === 'PUT')).toBe(false)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Change the setting' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ value: 1500 }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('sends nothing when the confirmation is cancelled', async () => {
    const { calls } = mockApi({ '/api/settings': SETTINGS })
    renderAt(<Settings />, '/settings')
    const field = await screen.findByLabelText('Claim agents at once')
    await userEvent.clear(field)
    await userEvent.type(field, '10')
    await userEvent.click(screen.getByRole('button', { name: 'Save claim agents at once' }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    expect(calls.some((c) => c.method === 'PUT')).toBe(false)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('changes the objectives as a pair', async () => {
    const { calls } = mockApi({ '/api/settings': SETTINGS, 'PUT /api/settings/objectives': {} })
    renderAt(<Settings />, '/settings')
    const grade = await screen.findByLabelText('Grade floor')
    await userEvent.clear(grade)
    await userEvent.type(grade, '1.5')
    await userEvent.click(screen.getByRole('button', { name: 'Save business objectives' }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Change the setting' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ value: { accuracy: 0.6, grade: 1.5 } }))
  })

  it("says what the backend refused, and keeps the page", async () => {
    mockApi({ '/api/settings': SETTINGS, 'PUT /api/settings/agents_at_once': { status: 422, body: { detail: 'claim agents at once must be a whole number from 1 to 25' } } })
    renderAt(<Settings />, '/settings')
    const field = await screen.findByLabelText('Claim agents at once')
    await userEvent.clear(field)
    await userEvent.type(field, '12')
    await userEvent.click(screen.getByRole('button', { name: 'Save claim agents at once' }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Change the setting' }))
    expect(await screen.findByText(/must be a whole number from 1 to 25/)).toBeInTheDocument()
  })

  it('does not offer to save a value outside the limits', async () => {
    mockApi({ '/api/settings': SETTINGS })
    renderAt(<Settings />, '/settings')
    const field = await screen.findByLabelText('Claim agents at once')
    await userEvent.clear(field)
    await userEvent.type(field, '30')
    expect(screen.getByRole('button', { name: 'Save claim agents at once' })).toBeDisabled()
  })

  it("is read-only for a handler who can't sign off over the limit, and says why", async () => {
    mockApi({ '/api/settings': SETTINGS })
    renderAt(<Settings />, '/settings', '*', MARCUS)
    expect(await screen.findByLabelText('Approval limit (USD)')).toBeDisabled()
    expect(screen.getByText(/Only a handler who can sign off payouts over the limit can change settings/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Save/ })).not.toBeInTheDocument()
  })

  it('lists the models allowed with prices right-aligned, and no way to change them', async () => {
    mockApi({ '/api/settings': SETTINGS })
    renderAt(<Settings />, '/settings')
    const table = within(await screen.findByRole('table', { name: 'Models allowed' }))
    expect(table.getByText('openai/gpt-6-luna')).toBeInTheDocument()
    expect(table.getByText('none, low, medium')).toBeInTheDocument()
    const price = table.getByText('$0.30')
    expect(price).toHaveClass('num')
    expect(screen.getByText(/dollars per million tokens/i)).toBeInTheDocument()
    expect(table.queryByRole('button')).not.toBeInTheDocument()
  })

  it('remembers the appearance in this browser', async () => {
    mockApi({ '/api/settings': SETTINGS })
    renderAt(<Settings />, '/settings')
    await userEvent.click(await screen.findByRole('radio', { name: 'Dark' }))
    expect(localStorage.getItem('marvin-theme')).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    await userEvent.click(screen.getByRole('radio', { name: 'Light' }))
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('lets anyone change the appearance, even a handler who cannot change settings', async () => {
    mockApi({ '/api/settings': SETTINGS })
    renderAt(<Settings />, '/settings', '*', MARCUS)
    await userEvent.click(await screen.findByRole('radio', { name: 'Dark' }))
    expect(document.documentElement.dataset.theme).toBe('dark')
  })
})
