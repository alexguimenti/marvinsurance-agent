import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { LatestDispatch, RunClaim } from '../api'
import RunPanel from './RunPanel'

const row = (id: string, over: Partial<RunClaim> = {}): RunClaim => ({
  claim_id: id,
  position: 1,
  status: 'in_progress',
  attempts: 0,
  decision: null,
  payout: null,
  payment: null,
  started_at: '2026-11-05T10:00:01+00:00',
  finished_at: null,
  cost_usd: null,
  seconds: 0,
  ...over,
})

const report = (over: Partial<LatestDispatch> = {}): LatestDispatch => ({
  run: { run_id: 4, kind: 'dispatch', status: 'running', workers: 3, model: 'deepseek/deepseek-v4.1-flash', reasoning_effort: 'low', eval_set: null, tiny: null, error: null, started_at: '2026-11-05T10:00:00+00:00', finished_at: null },
  claims: [row('CLM-1001'), row('CLM-1002', { status: 'waiting', started_at: null })],
  totals: { calls: 6, input_tokens: 900, output_tokens: 90, reasoning_tokens: 0, cost_usd: 0.0123, seconds: 42.5 },
  summary: null,
  ...over,
})

const show = (data: LatestDispatch) => render(<MemoryRouter><RunPanel data={data} /></MemoryRouter>)

describe('RunPanel', () => {
  it('says there is no dispatch yet', () => {
    show({ run: null, claims: [], totals: null, summary: null })
    expect(screen.getByText(/No dispatch yet/)).toBeInTheDocument()
  })

  it('shows each claim of the dispatch and where it stands', () => {
    show(report())
    expect(screen.getByText((_, el) => el?.tagName === 'P' && /^Dispatch 4 · Running · 3 claim agents at once/.test(el.textContent ?? ''))).toBeInTheDocument()
    const first = within(screen.getByRole('link', { name: 'CLM-1001' }).closest('li')!)
    expect(screen.getByRole('link', { name: 'CLM-1001' })).toHaveAttribute('href', '/claims/CLM-1001')
    expect(first.getByText('In progress')).toBeInTheDocument()
    const second = within(screen.getByRole('link', { name: 'CLM-1002' }).closest('li')!)
    expect(second.getByText('Waiting')).toBeInTheDocument()
  })

  it('shows the model cost and time of the dispatch', () => {
    show(report())
    expect(screen.getByText(/\$0\.01 in model calls/)).toBeInTheDocument()
    expect(screen.getByText(/42\.5 s of agent time/)).toBeInTheDocument()
  })

  it('shows the summary from the records when it finishes', () => {
    const finished = report({
      run: { ...report().run!, status: 'finished', finished_at: '2026-11-05T10:01:00+00:00' },
      summary: {
        approved: 19, declined: 3, referred: 2,
        awaiting_sign_off: [{ claim_id: 'CLM-1003', payout: 3750 }],
        left_for_handler: ['CLM-1013'], in_progress: ['CLM-1020'],
      },
    })
    show(finished)
    const summary = within(document.querySelector('.run-summary') as HTMLElement)
    expect(summary.getByText(/19 approved · 3 declined · 2 referred/)).toBeInTheDocument()
    expect(summary.getByText('CLM-1003')).toBeInTheDocument()
    expect(summary.getByText('$3,750.00')).toBeInTheDocument()
    expect(summary.getByText('CLM-1013')).toBeInTheDocument()
    expect(summary.getByText('CLM-1020')).toBeInTheDocument()
  })

  it('says a stopped dispatch is carried on by the next one', () => {
    show(report({ run: { ...report().run!, status: 'stopped' }, summary: { approved: 0, declined: 0, referred: 0, awaiting_sign_off: [], left_for_handler: [], in_progress: ['CLM-1001'] } }))
    expect(screen.getByText(/stopped.*next dispatch carries on/i)).toBeInTheDocument()
  })
})
