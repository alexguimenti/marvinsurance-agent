import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { MARCUS, mockApi, renderAt } from '../testUtils'
import Improvement from './Improvement'

const base = {
  round_id: 3, stage: 'improving', progress: null, open: true, recipe_version: 1,
  settings: { round_spending_limit: 0.5, agents_at_once: 25 }, version_tried: null, reason: null, verdict: null, why: null,
  before: null, after: null, started_by: 'HND-01', started_by_name: 'Dana Whitfield', stopped_by: null, stopped_by_name: null,
  error: null, started_at: '2026-11-06T10:00:00+00:00', finished_at: null, spent_usd: 0.1234, spending_limit: 0.5,
  proposals: [], waiting: null,
}
const round = (over = {}) => ({ ...base, ...over })

const change = {
  id: 11, kind: 'change', reason: 'The misses are approvals with documents missing.',
  lines: [{ kind: 'same', text: 'Decide the claim.' }, { kind: 'add', text: '- Refer a claim whose receipt is missing.' }],
  model: { before: 'deepseek/deepseek-v4.1-flash', after: 'openai/gpt-6-luna' }, reasoning_effort: { before: 'low', after: 'low' },
}

const CONFIG = { current: {}, current_recipe: 2, versions: [], recipes: [
  { version: 1, summary: "The lab's first recipe", reason: 'The starting point', proposed_by: 'seed', approved_by: 'seed', approved_by_name: null, approved_at: '2026-11-05T22:00:00+00:00', round_id: null, created_at: '' },
  { version: 2, summary: '1 line added, 0 removed', reason: 'why', proposed_by: 'improver', approved_by: 'HND-01', approved_by_name: 'Dana Whitfield', approved_at: '2026-11-06T09:00:00+00:00', round_id: 2, created_at: '' },
] }

const SETTINGS = { settings: [{ name: 'round_spending_limit', value: 0.5, changed_by: 'seed', changed_by_name: null, changed_at: '' }], models_allowed: {} }

const api = (rounds: unknown[], extra: Record<string, unknown> = {}) => ({ '/api/rounds': rounds, '/api/configuration': CONFIG, '/api/settings': SETTINGS, ...extra })

describe('Improvement', () => {
  it("shows the open round's stage as it happens, with its progress and what it has spent", async () => {
    mockApi(api([round({ stage: 'evaluating', progress: '2 of 3' })]))
    renderAt(<Improvement />, '/improvement')
    const open = await screen.findByRole('region', { name: 'Open round' })
    expect(within(open).getByText('Round 3')).toBeInTheDocument()
    expect(within(open).getByText(/Evaluating the new version/)).toBeInTheDocument()
    expect(within(open).getByText('2 of 3')).toBeInTheDocument()
    expect(within(open).getByText(/\$0\.12 of \$0\.50/)).toBeInTheDocument()
    expect(within(open).getByRole('list', { name: 'Steps of a round' })).toBeInTheDocument()
  })

  it('shows every step of a round and which one it has reached', async () => {
    mockApi(api([round({ stage: 'awaiting_change', waiting: change })]))
    renderAt(<Improvement />, '/improvement')
    const steps = within(await screen.findByRole('list', { name: 'Steps of a round' })).getAllByRole('listitem')
    expect(steps.map((s) => s.textContent)).toEqual(expect.arrayContaining([
      expect.stringContaining('First evaluations'), expect.stringContaining('The improver at work'), expect.stringContaining('A change waiting for approval'),
      expect.stringContaining('Evaluating the new version'), expect.stringContaining('Verdict'), expect.stringContaining('A recipe waiting for approval'), expect.stringContaining('Finished'),
    ]))
    expect(steps.find((s) => s.getAttribute('aria-current') === 'step')).toHaveTextContent('A change waiting for approval')
  })

  it('shows a waiting change as a diff, with the model and effort before and after', async () => {
    mockApi(api([round({ stage: 'awaiting_change', waiting: change })]))
    renderAt(<Improvement />, '/improvement')
    const card = await screen.findByRole('region', { name: 'Waiting for your decision' })
    expect(within(card).getByText('- Refer a claim whose receipt is missing.')).toBeInTheDocument()
    expect(within(card).getByText(/deepseek\/deepseek-v4\.1-flash → openai\/gpt-6-luna/)).toBeInTheDocument()
    expect(within(card).getByText(/The misses are approvals with documents missing/)).toBeInTheDocument()
  })

  it('approves a proposal after the confirmation', async () => {
    const { calls } = mockApi(api([round({ stage: 'awaiting_change', waiting: change })], { 'POST /api/rounds/3/decision': { status: 'decided' } }))
    renderAt(<Improvement />, '/improvement')
    await userEvent.click(await screen.findByRole('button', { name: 'Approve' }))
    expect(calls.some((c) => c.method === 'POST')).toBe(false)
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Approve the change' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ proposal_id: 11, approve: true, reason: '' }))
  })

  it('needs a reason to reject, and sends it', async () => {
    const { calls } = mockApi(api([round({ stage: 'awaiting_change', waiting: change })], { 'POST /api/rounds/3/decision': { status: 'decided' } }))
    renderAt(<Improvement />, '/improvement')
    await userEvent.click(await screen.findByRole('button', { name: 'Reject' }))
    const dialog = await screen.findByRole('dialog')
    const confirm = within(dialog).getByRole('button', { name: 'Reject the change' })
    expect(confirm).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText('Why are you rejecting it?'), 'Name the section.')
    expect(confirm).toBeEnabled()
    await userEvent.click(confirm)
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ proposal_id: 11, approve: false, reason: 'Name the section.' }))
  })

  it('shows a waiting recipe as a diff too', async () => {
    const recipe = { id: 12, kind: 'recipe', reason: 'Read earlier rounds first.', lines: [{ kind: 'add', text: '6. Read the earlier rounds.' }] }
    mockApi(api([round({ stage: 'awaiting_recipe', waiting: recipe })]))
    renderAt(<Improvement />, '/improvement')
    const card = await screen.findByRole('region', { name: 'Waiting for your decision' })
    expect(within(card).getByText('6. Read the earlier rounds.')).toBeInTheDocument()
    expect(within(card).getByText(/new recipe/i)).toBeInTheDocument()
  })

  it("tells a handler who can't sign off over the limit what they can't do and why", async () => {
    mockApi(api([round({ stage: 'awaiting_change', waiting: change })]))
    renderAt(<Improvement />, '/improvement', '*', MARCUS)
    expect(await screen.findByText(/Only a handler who can sign off payouts over the limit can start or stop a round or approve what the improver proposes/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start round' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop round' })).not.toBeInTheDocument()
    expect(screen.getByText('- Refer a claim whose receipt is missing.')).toBeInTheDocument() // they can still read it
  })

  it('starts a round after the confirmation', async () => {
    const { calls } = mockApi(api([], { 'POST /api/rounds': { status: 'started', round_id: 1 } }))
    renderAt(<Improvement />, '/improvement')
    await userEvent.click(await screen.findByRole('button', { name: 'Start round' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/up to \$0\.50/)).toBeInTheDocument()
    expect(calls.some((c) => c.method === 'POST')).toBe(false)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Start the round' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/rounds')).toBe(true))
  })

  it('cannot start a second round while one is open, and says why', async () => {
    mockApi(api([round()]))
    renderAt(<Improvement />, '/improvement')
    const start = await screen.findByRole('button', { name: 'Start round' })
    expect(start).toBeDisabled()
    expect(screen.getByText(/Round 3 is open/)).toBeInTheDocument()
  })

  it('stops the open round after the confirmation', async () => {
    const { calls } = mockApi(api([round()], { 'POST /api/rounds/3/stop': { stage: 'stopped' } }))
    renderAt(<Improvement />, '/improvement')
    await userEvent.click(await screen.findByRole('button', { name: 'Stop round' }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Stop the round' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/rounds/3/stop')).toBe(true))
  })

  it('lists the rounds newest first with the scores before and after, the verdict, who decided and the cost', async () => {
    const finished = (id: number, verdict: string, over = {}) => round({
      round_id: id, stage: 'finished', open: false, version_tried: id, verdict, why: verdict === 'kept' ? 'accuracy improved' : 'accuracy got worse',
      before: { accuracy: 0.7, grade: 1.5 }, after: { accuracy: verdict === 'kept' ? 0.82 : 0.6, grade: 1.8 }, spent_usd: 0.2,
      proposals: [{ id: id * 10, kind: 'change', reason: 'r', status: 'approved', decided_by: 'HND-01', decided_by_name: 'Dana Whitfield', decided_at: '2026-11-06T10:05:00+00:00', decision_reason: null, created_at: '' }],
      ...over,
    })
    mockApi(api([finished(2, 'rolled_back'), finished(1, 'kept')]))
    renderAt(<Improvement />, '/improvement')
    const rows = within(await screen.findByRole('table', { name: 'Rounds' })).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText('Round 2')).toBeInTheDocument()
    expect(within(rows[0]).getByText('Rolled back')).toBeInTheDocument()
    expect(within(rows[0]).getByText(/accuracy got worse/)).toBeInTheDocument()
    expect(within(rows[1]).getByText('Kept')).toBeInTheDocument()
    expect(within(rows[1]).getByText('70% → 82%')).toBeInTheDocument()
    expect(within(rows[1]).getByText('1.50 → 1.80')).toBeInTheDocument()
    expect(within(rows[1]).getByText(/Dana Whitfield approved the change/)).toBeInTheDocument()
    expect(within(rows[1]).getByText('$0.20')).toBeInTheDocument()
  })

  it('shows a rejection with its reason, and a round that was stopped', async () => {
    const stopped = round({ stage: 'stopped', open: false, stopped_by_name: 'Dana Whitfield', proposals: [
      { id: 1, kind: 'change', reason: 'r', status: 'rejected', decided_by: 'HND-01', decided_by_name: 'Dana Whitfield', decided_at: '2026-11-06T10:05:00+00:00', decision_reason: 'Too broad', created_at: '' }] })
    mockApi(api([stopped]))
    renderAt(<Improvement />, '/improvement')
    const row = within(await screen.findByRole('table', { name: 'Rounds' })).getAllByRole('row')[1]
    expect(within(row).getByText('Stopped')).toBeInTheDocument()
    expect(within(row).getByText(/Dana Whitfield rejected the change: Too broad/)).toBeInTheDocument()
  })

  it('says what to do when there are no rounds yet', async () => {
    mockApi(api([]))
    renderAt(<Improvement />, '/improvement')
    expect(await screen.findByText(/No rounds yet/)).toBeInTheDocument()
    expect(screen.getByText(/No version has been tried yet/)).toBeInTheDocument()
  })

  it('draws the held-out scores of each version tried', async () => {
    mockApi(api([round({ round_id: 1, stage: 'finished', open: false, version_tried: 2, verdict: 'kept', why: 'accuracy improved', before: { accuracy: 0.7, grade: 1.5 }, after: { accuracy: 0.82, grade: 1.8 } })]))
    renderAt(<Improvement />, '/improvement')
    expect(await screen.findByRole('img', { name: 'Held-out accuracy of each version tried' })).toBeInTheDocument()
  })

  it("lists the recipe's versions and shows their changes", async () => {
    mockApi(api([], { '/api/configuration/recipe/2/diff': { kind: 'recipe', version: 2, against: 1, summary: '1 line added, 0 removed', lines: [{ kind: 'add', text: '6. A new step.' }] } }))
    renderAt(<Improvement />, '/improvement')
    const table = await screen.findByRole('table', { name: 'Recipe versions' })
    await userEvent.click(within(table).getByRole('button', { name: 'Show changes in recipe 2' }))
    expect(await screen.findByText('6. A new step.')).toBeInTheDocument()
  })

  it('says what to do when it cannot load', async () => {
    mockApi({ '/api/rounds': { status: 500, body: { detail: 'boom' } }, '/api/configuration': CONFIG, '/api/settings': SETTINGS })
    renderAt(<Improvement />, '/improvement')
    expect(await screen.findByText(/Could not load the rounds/)).toBeInTheDocument()
  })

  it('shows what stopped a round that ended with an error', async () => {
    mockApi(api([round({ stage: 'finished', open: false, error: 'The round could not afford the 3 evaluations version 1 still needed.' })]))
    renderAt(<Improvement />, '/improvement')
    expect(await screen.findByText(/could not afford the 3 evaluations/)).toBeInTheDocument()
  })
})
