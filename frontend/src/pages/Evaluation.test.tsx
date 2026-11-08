import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { mockApi, renderAt } from '../testUtils'
import Evaluation from './Evaluation'

const row = (copy: string, agent: { decision: string; payout: number } | null, handler: { decision: string; payout: number }, ok: boolean, cost = 0) => ({
  claim_id: `E7-${copy}`,
  copy_of: copy,
  status: 'decided',
  agent: agent && { ...agent, rationale: 'Section 10 applies.' },
  handler,
  decision_ok: ok,
  accurate: ok && cost === 0,
  mistake_cost: cost,
  referred: (agent?.decision ?? 'refer') === 'refer',
  cost_usd: 0.002,
  seconds: 12.5,
  score: agent ? (ok ? 2.4 : 0.6) : 0,
  letter: agent ? (ok ? 'B' : 'F') : 'F',
  same_grounds: ok ? 0.9 : 0.1,
  same_section: ok ? 0.8 : 0.2,
  judge_cost_usd: 0.0001,
})

const report = {
  run: { run_id: 7, kind: 'evaluation', status: 'finished', workers: 5, model: 'm', reasoning_effort: 'low', eval_set: 'evaluation', tiny: true, error: null, started_at: '2026-11-05T10:00:00+00:00', finished_at: '2026-11-05T10:01:00+00:00' },
  measures: { claims: 3, decision_accuracy: 2 / 3, accuracy: 1 / 3, grade: 1.0, judge_cost_usd: 0.0003, mistake_cost: 1550, straight_through: 2 / 3, cost_per_claim: 0.002, seconds_per_claim: 12.5, total_seconds: 30 },
  claims: [
    row('CLM-1007', { decision: 'approve', payout: 420 }, { decision: 'approve', payout: 420 }, true),
    row('CLM-1015', { decision: 'decline', payout: 0 }, { decision: 'approve', payout: 900 }, false, 1500),
    row('CLM-1017', null, { decision: 'approve', payout: 300 }, false, 50),
  ],
}

describe('Evaluation', () => {
  it("lists its claims with the handler's decision and payout beside the agent's", async () => {
    mockApi({ '/api/evaluations/7': report })
    renderAt(<Evaluation />, '/scoreboard/7', '/scoreboard/:id')
    expect(await screen.findByRole('heading', { name: 'Evaluation 7' })).toBeInTheDocument()
    const rows = screen.getAllByRole('row').slice(1)
    expect(rows).toHaveLength(3)
    const second = within(rows[1])
    expect(second.getByText('Decline')).toBeInTheDocument()
    expect(second.getByText('Approve')).toBeInTheDocument()
    expect(second.getByText('$900.00')).toBeInTheDocument()
    expect(second.getByText('$1,500.00')).toBeInTheDocument()
  })

  it('marks the misses and links each claim to its page', async () => {
    mockApi({ '/api/evaluations/7': report })
    renderAt(<Evaluation />, '/scoreboard/7', '/scoreboard/:id')
    await screen.findByRole('heading', { name: 'Evaluation 7' })
    const rows = screen.getAllByRole('row').slice(1)
    expect(rows[0]).not.toHaveClass('miss')
    expect(rows[1]).toHaveClass('miss')
    expect(rows[2]).toHaveClass('miss')
    expect(within(rows[0]).getByRole('link', { name: 'CLM-1007' })).toHaveAttribute('href', '/claims/E7-CLM-1007')
    expect(within(rows[2]).getByText('No decision')).toBeInTheDocument()
  })

  it("shows each claim's grade with the two checks as yes or no", async () => {
    mockApi({ '/api/evaluations/7': report })
    renderAt(<Evaluation />, '/scoreboard/7', '/scoreboard/:id')
    await screen.findByRole('heading', { name: 'Evaluation 7' })
    const rows = screen.getAllByRole('row').slice(1)
    const first = within(rows[0])
    expect(first.getByText('B')).toHaveClass('pill')
    expect(first.getByText('2.40')).toBeInTheDocument()
    expect(first.getByLabelText('Same grounds: yes')).toBeInTheDocument()
    expect(first.getByLabelText('Same section: yes')).toBeInTheDocument()
    const second = within(rows[1])
    expect(second.getByLabelText('Same grounds: no')).toBeInTheDocument()
    expect(second.getByLabelText('Same section: no')).toBeInTheDocument()
  })

  it('says a claim the judge could not grade is not graded', async () => {
    const ungraded = { ...report.claims[0], score: null, letter: null, same_grounds: null, same_section: null }
    mockApi({ '/api/evaluations/7': { ...report, claims: [ungraded] } })
    renderAt(<Evaluation />, '/scoreboard/7', '/scoreboard/:id')
    await screen.findByRole('heading', { name: 'Evaluation 7' })
    expect(within(screen.getAllByRole('row')[1]).getByText('Not graded')).toBeInTheDocument()
  })

  it('shows its measures', async () => {
    mockApi({ '/api/evaluations/7': report })
    renderAt(<Evaluation />, '/scoreboard/7', '/scoreboard/:id')
    const tile = async (name: string) => (await screen.findByRole('group', { name })).querySelector('.tile-n')?.textContent
    expect(await tile('Decision accuracy')).toBe('67%')
    expect(await tile('Accuracy')).toBe('33%')
    expect(await tile('Grade')).toBe('1.00')
    expect(await tile('Cost of mistakes')).toBe('$1,550.00')
  })

  it('says so when there is no such evaluation', async () => {
    mockApi({ '/api/evaluations/7': { status: 404, body: { detail: 'no evaluation 7' } } })
    renderAt(<Evaluation />, '/scoreboard/7', '/scoreboard/:id')
    expect(await screen.findByText(/Could not load this evaluation/)).toBeInTheDocument()
  })
})
