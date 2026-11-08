import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { mockApi, renderAt } from '../testUtils'
import Configuration from './Configuration'

const version = (n: number, over = {}) => ({
  version: n, instructions: `Instructions of version ${n}`, model: 'deepseek/deepseek-v4.1-flash', reasoning_effort: 'low',
  summary: n === 1 ? 'The claim agent as it was before versions' : '2 lines added, 0 removed', reason: n === 1 ? 'The starting point' : 'Misses approvals with no receipts',
  proposed_by: n === 1 ? 'seed' : 'improver', approved_by: n === 1 ? 'seed' : 'HND-01', approved_by_name: n === 1 ? null : 'Dana Whitfield',
  approved_at: '2026-11-05T22:04:00+00:00', round_id: n === 1 ? null : 1, created_at: '2026-11-05T22:00:00+00:00',
  scores: n === 1 ? { 'held-out': { accuracy: 0.8, grade: 1.6, runs: 2 } } : { 'held-out': { accuracy: 0.88, grade: 1.9, runs: 2 } },
  ...over,
})

const recipe = (n: number) => ({
  version: n, recipe: `Recipe ${n}`, summary: n === 1 ? "The lab's first recipe" : '1 line added, 0 removed', reason: 'why', proposed_by: n === 1 ? 'seed' : 'improver',
  approved_by: n === 1 ? 'seed' : 'HND-01', approved_by_name: n === 1 ? null : 'Dana Whitfield', approved_at: '2026-11-05T22:04:00+00:00', round_id: null, created_at: '',
})

const CONFIG = { current: version(2), current_recipe: 1, versions: [version(1), version(2)], recipes: [recipe(1), recipe(2)] }

describe('Configuration', () => {
  it('shows the current version and every version with its held-out scores', async () => {
    mockApi({ '/api/configuration': CONFIG })
    renderAt(<Configuration />, '/configuration')
    const current = await screen.findByRole('region', { name: 'Current version' })
    expect(within(current).getByText('Version 2')).toBeInTheDocument()
    expect(within(current).getByText(/Instructions of version 2/)).toBeInTheDocument()
    expect(within(current).getByText(/deepseek\/deepseek-v4\.1-flash/)).toBeInTheDocument()
    const rows = within(screen.getByRole('table', { name: 'Versions' })).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText('80%')).toBeInTheDocument()
    expect(within(rows[0]).getByText('1.60')).toBeInTheDocument()
    expect(within(rows[1]).getByText('88%')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Current')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Dana Whitfield')).toBeInTheDocument()
  })

  it('shows no scores for a version no round has evaluated', async () => {
    mockApi({ '/api/configuration': { ...CONFIG, versions: [version(1, { scores: {} }), version(2, { scores: {} })] } })
    renderAt(<Configuration />, '/configuration')
    const rows = within(await screen.findByRole('table', { name: 'Versions' })).getAllByRole('row').slice(1)
    expect(within(rows[0]).getAllByText('—').length).toBeGreaterThanOrEqual(2)
  })

  it("opens a version's changes from the one before", async () => {
    const { calls } = mockApi({
      '/api/configuration': CONFIG,
      '/api/configuration/config/2/diff': {
        kind: 'config', version: 2, against: 1, summary: '2 lines added, 0 removed',
        lines: [{ kind: 'same', text: 'kept' }, { kind: 'add', text: 'Never approve without receipts.' }],
        model: { before: 'deepseek/deepseek-v4.1-flash', after: 'deepseek/deepseek-v4.1-flash' }, reasoning_effort: { before: 'low', after: 'low' },
      },
    })
    renderAt(<Configuration />, '/configuration')
    await userEvent.click(await screen.findByRole('button', { name: 'Show changes in version 2' }))
    expect(await screen.findByText('Never approve without receipts.')).toBeInTheDocument()
    expect(screen.getByText(/compared with version 1/i)).toBeInTheDocument()
    expect(calls.some((c) => c.url === '/api/configuration/config/2/diff')).toBe(true)
  })

  it('lists the recipe versions, and opens their changes in the same view', async () => {
    mockApi({
      '/api/configuration': CONFIG,
      '/api/configuration/recipe/2/diff': {
        kind: 'recipe', version: 2, against: 1, summary: '1 line added, 0 removed', lines: [{ kind: 'add', text: 'Look at the weakest explanations first.' }],
      },
    })
    renderAt(<Configuration />, '/configuration')
    const rows = within(await screen.findByRole('table', { name: 'Recipe versions' })).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    await userEvent.click(within(rows[1]).getByRole('button', { name: 'Show changes in recipe 2' }))
    expect(await screen.findByText('Look at the weakest explanations first.')).toBeInTheDocument()
  })

  it('says what to do when it cannot load', async () => {
    mockApi({ '/api/configuration': { status: 500, body: { detail: 'boom' } } })
    renderAt(<Configuration />, '/configuration')
    expect(await screen.findByText(/Could not load the configuration/)).toBeInTheDocument()
    expect(screen.getByText(/reload/i)).toBeInTheDocument()
  })
})
