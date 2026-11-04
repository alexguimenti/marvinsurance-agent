import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Run, TodoItem } from '../api'
import TodoPanel from './TodoPanel'

const run = (over: Partial<Run> = {}): Run => ({
  run_id: 3,
  status: 'running',
  alive: true,
  cap: 5,
  model: 'deepseek/deepseek-v4.1-flash',
  summary: null,
  error: null,
  usage: null,
  started_at: '2026-10-30 09:00:00',
  finished_at: null,
  ...over,
})

const item = (position: number, status: TodoItem['status'], note: string | null = null): TodoItem => ({
  item_id: position,
  position,
  title: `Work CLM-100${position}`,
  status,
  claim_id: `CLM-100${position}`,
  note,
})

describe('TodoPanel', () => {
  it('says there is no run yet', () => {
    render(<TodoPanel run={null} todo={[]} />)
    expect(screen.getByText(/No run yet/)).toBeInTheDocument()
  })

  it('shows every item with its status and note', () => {
    render(
      <TodoPanel
        run={run()}
        todo={[
          item(1, 'done', 'approve $420.00, paid'),
          item(2, 'in_progress'),
          item(3, 'todo'),
          item(4, 'left_for_handler', 'RuntimeError: down'),
        ]}
      />,
    )
    const rows = screen.getAllByRole('listitem')
    expect(rows).toHaveLength(4)
    expect(within(rows[0]).getByText('Done')).toBeInTheDocument()
    expect(within(rows[0]).getByText('approve $420.00, paid')).toBeInTheDocument()
    expect(within(rows[1]).getByText('In progress')).toBeInTheDocument()
    expect(within(rows[2]).getByText('To do')).toBeInTheDocument()
    expect(within(rows[3]).getByText('Left for a handler')).toBeInTheDocument()
    expect(screen.getByText(/1 of 4 done/)).toBeInTheDocument()
  })

  it('shows the run status, cap, cost and summary', () => {
    render(
      <TodoPanel
        run={run({
          status: 'finished',
          summary: 'Worked 5 claims.',
          usage: { calls: 12, input_tokens: 1000, output_tokens: 100, reasoning_tokens: 0, cost_usd: 0.0123 },
        })}
        todo={[item(1, 'done')]}
      />,
    )
    expect(screen.getByText(/Run 3/)).toBeInTheDocument()
    expect(screen.getByText(/Finished/)).toBeInTheDocument()
    expect(screen.getByText(/capped at 5/)).toBeInTheDocument()
    expect(screen.getByText(/\$0\.01/)).toBeInTheDocument()
    expect(screen.getByText('Worked 5 claims.')).toBeInTheDocument()
  })

  it('shows why a run failed', () => {
    render(<TodoPanel run={run({ status: 'failed', error: 'the server stopped during the run' })} todo={[]} />)
    expect(screen.getByText(/the server stopped during the run/)).toBeInTheDocument()
  })
})
