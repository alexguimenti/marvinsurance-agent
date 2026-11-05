import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { mockApi, renderAt } from '../testUtils'
import Scoreboard from './Scoreboard'

const evaluation = (run_id: number, over = {}) => ({
  run: { run_id, kind: 'evaluation', status: 'finished', workers: 5, model: 'deepseek/deepseek-v4.1-flash', reasoning_effort: 'low', eval_set: 'evaluation', tiny: false, error: null, started_at: '2026-11-05T10:00:00+00:00', finished_at: '2026-11-05T10:01:00+00:00', ...over },
  measures: { claims: 25, decision_accuracy: 0.96, accuracy: 0.88, mistake_cost: 1550, straight_through: 0.76, cost_per_claim: 0.0021, seconds_per_claim: 14.2, total_seconds: 58.3 },
})

describe('Scoreboard', () => {
  it('lists the evaluations newest first with every measure', async () => {
    mockApi({ '/api/evaluations': [evaluation(9, { eval_set: 'held_out', tiny: true }), evaluation(4)] })
    renderAt(<Scoreboard />, '/scoreboard')
    const rows = await screen.findAllByRole('row')
    for (const name of ['Decision accuracy', 'Accuracy', 'Cost of mistakes', 'Straight-through', 'Cost per claim', 'Time per claim', 'Total time']) {
      expect(screen.getByRole('columnheader', { name })).toBeInTheDocument()
    }
    const first = within(rows[1])
    expect(first.getByRole('link', { name: 'Evaluation 9' })).toHaveAttribute('href', '/scoreboard/9')
    expect(first.getByText('Held-out set, tiny')).toBeInTheDocument()
    expect(first.getByText('96%')).toBeInTheDocument()
    expect(first.getByText('88%')).toBeInTheDocument()
    expect(first.getByText('$1,550.00')).toBeInTheDocument()
    expect(first.getByText('76%')).toBeInTheDocument()
    expect(first.getByText('$0.0021')).toBeInTheDocument()
    expect(first.getByText('14.2 s')).toBeInTheDocument()
    expect(first.getByText('58.3 s')).toBeInTheDocument()
    expect(within(rows[2]).getByRole('link', { name: 'Evaluation 4' })).toBeInTheDocument()
  })

  it('records the model, the reasoning effort and N of each evaluation', async () => {
    mockApi({ '/api/evaluations': [evaluation(4, { reasoning_effort: 'none', model: 'other/model' })] })
    renderAt(<Scoreboard />, '/scoreboard')
    expect(await screen.findByText(/other\/model · effort none · 5 at once/)).toBeInTheDocument()
  })

  it('shows a stopped evaluation as stopped', async () => {
    mockApi({ '/api/evaluations': [evaluation(4, { status: 'stopped' })] })
    renderAt(<Scoreboard />, '/scoreboard')
    expect(await screen.findByText('Stopped')).toBeInTheDocument()
  })

  it('starts an evaluation with the set, tiny and N chosen', async () => {
    const { calls } = mockApi({ '/api/evaluations': [], 'POST /api/evaluations': { status: 'started' } })
    renderAt(<Scoreboard />, '/scoreboard')
    expect(await screen.findByLabelText('Claim agents at once')).toHaveValue(3)
    await userEvent.selectOptions(screen.getByLabelText('Set'), 'held_out')
    await userEvent.click(screen.getByLabelText('Only the five tiny claims'))
    await userEvent.clear(screen.getByLabelText('Claim agents at once'))
    await userEvent.type(screen.getByLabelText('Claim agents at once'), '5')
    await userEvent.click(screen.getByRole('button', { name: 'Start evaluation' }))
    await waitFor(() =>
      expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ eval_set: 'held_out', tiny: true, workers: 5 }),
    )
  })

  it('says so when a run is already going', async () => {
    mockApi({ '/api/evaluations': [], 'POST /api/evaluations': { status: 409, body: { detail: 'busy' } } })
    renderAt(<Scoreboard />, '/scoreboard')
    await userEvent.click(await screen.findByRole('button', { name: 'Start evaluation' }))
    expect(await screen.findByText('A dispatch or evaluation is already running.')).toBeInTheDocument()
  })

  it('says there are no evaluations yet', async () => {
    mockApi({ '/api/evaluations': [] })
    renderAt(<Scoreboard />, '/scoreboard')
    expect(await screen.findByText(/No evaluations yet/)).toBeInTheDocument()
  })
})
