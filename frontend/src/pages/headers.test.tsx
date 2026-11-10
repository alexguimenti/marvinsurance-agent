import { screen } from '@testing-library/react'
import type { ComponentType } from 'react'
import { describe, expect, it } from 'vitest'
import { mockApi, renderAt } from '../testUtils'
import Approvals from './Approvals'
import ClaimDetail from './ClaimDetail'
import Configuration from './Configuration'
import Evaluation from './Evaluation'
import Improvement from './Improvement'
import Queue from './Queue'
import Scoreboard from './Scoreboard'
import Settings from './Settings'

const row = (name: string, value: unknown) => ({ name, value, changed_by: 'seed', changed_by_name: null, changed_at: '2026-11-05T22:04:00+00:00' })
const SETTINGS = {
  settings: [
    row('approval_limit', 1000),
    row('objectives', { accuracy: 0.6, grade: 1.0 }),
    row('noise', { accuracy: 0.1, grade: 0.15, cost_per_claim: 0.15, seconds_per_claim: 0.15 }),
    row('tolerance', { accuracy: 0.1, grade: 0.15, cost_per_claim: 1.0, seconds_per_claim: 0.25 }),
    row('round_spending_limit', 0.5),
    row('agents_at_once', 25),
  ],
  models_allowed: {},
}
const NO_RUN = { run: null, claims: [], totals: null, summary: null }
const EMPTY = {
  '/api/claims': [], '/api/dispatches/latest': NO_RUN, '/api/approvals': [], '/api/evaluations': [], '/api/rounds': [],
  '/api/settings': SETTINGS,
  '/api/configuration': { current: { version: 1, instructions: '', model: 'm', reasoning_effort: 'low' }, current_recipe: 1, versions: [], recipes: [] },
}

const PAGES: [string, ComponentType, string, string][] = [
  ['Claims queue', Queue, '/', '*'],
  ['Approvals', Approvals, '/approvals', '*'],
  ['Scoreboard', Scoreboard, '/scoreboard', '*'],
  ['Improvement', Improvement, '/improvement', '*'],
  ['Configuration', Configuration, '/configuration', '*'],
  ['Settings', Settings, '/settings', '*'],
]

describe('every page starts with a header that says what it is for', () => {
  it.each(PAGES)('%s', async (title, page, path, route) => {
    mockApi(EMPTY)
    const Page = page
    const { container } = renderAt(<Page />, path, route)
    expect(await screen.findByRole('heading', { level: 1, name: title })).toBeInTheDocument()
    expect(container.querySelector('.page-head .eyebrow')?.textContent).toBeTruthy()
    expect((container.querySelector('.page-head .lede')?.textContent ?? '').length).toBeGreaterThan(20)
  })

  it('an evaluation', async () => {
    mockApi({ '/api/evaluations/7': {
      run: { run_id: 7, kind: 'evaluation', status: 'finished', workers: 5, model: 'm', reasoning_effort: 'low', eval_set: 'evaluation', tiny: true, error: null, started_at: '2026-11-05T10:00:00+00:00', finished_at: null },
      measures: { claims: 0, decision_accuracy: null, accuracy: null, grade: null, judge_cost_usd: 0, mistake_cost: 0, straight_through: null, cost_per_claim: null, seconds_per_claim: null, total_seconds: 0 }, claims: [] } })
    const { container } = renderAt(<Evaluation />, '/scoreboard/7', '/scoreboard/:id')
    expect(await screen.findByRole('heading', { level: 1, name: 'Evaluation 7' })).toBeInTheDocument()
    expect((container.querySelector('.page-head .lede')?.textContent ?? '').length).toBeGreaterThan(10)
  })

  it('a claim', async () => {
    mockApi({ '/api/claims/CLM-1': { status: 'waiting', claim: { claim_id: 'CLM-1', claim_type: 'baggage', story: 's', amount_claimed: 1, items: [], documents: [], incident_date: '', incident_location: '', summary: '', submitted_on: '' },
      policy: { policy_id: 'P', plan: 'Premium', add_ons: [], travelers: [] }, customer: { name: 'A', previous_claims: [] }, decision: null, payment: null, usage: null, attempts: 0, last_error: null, context_used: [] },
      '/api/claims/CLM-1/trace': [] })
    const { container } = renderAt(<ClaimDetail />, '/claims/CLM-1', '/claims/:id')
    expect(await screen.findByRole('heading', { level: 1, name: 'CLM-1' })).toBeInTheDocument()
    expect(container.querySelector('.page-head .lede')?.textContent).toBeTruthy()
  })
})
