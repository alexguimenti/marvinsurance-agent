import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { step } from './fixtures'
import StepCard from './StepCard'

afterEach(() => vi.useRealTimers())

describe('a step card', () => {
  it('shows a working card with a spinner and the seconds so far, counting up', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-01-01T10:00:00Z'))
    render(<StepCard step={step({ status: 'working', seconds: null, started_at: '2026-01-01T10:00:00Z' })} />)
    expect(screen.getByText('get_claim')).toBeInTheDocument()
    expect(screen.getByText('CLM-1012')).toBeInTheDocument()
    expect(screen.getByText(/^0 s$/)).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(screen.getByText(/^3 s$/)).toBeInTheDocument()
    expect(document.querySelector('.cp-spinner')).not.toBeNull()
  })

  it('shows a done card with the time it took', () => {
    render(<StepCard step={step({ status: 'done', seconds: 1.234 })} />)
    expect(screen.getByText('done · 1.2 s')).toBeInTheDocument()
    expect(document.querySelector('.cp-spinner')).toBeNull()
  })

  it('shows the reason on a refused or failed card', () => {
    render(<StepCard step={step({ status: 'refused', tool: 'start_dispatch', kind: 'action', main_input: '3 agents', reason: 'a dispatch is already running', output: null })} />)
    expect(screen.getByText('refused')).toBeInTheDocument()
    expect(screen.getByText('a dispatch is already running')).toBeInTheDocument()
  })

  it('opens to show what was asked and what came back, trimmed, with the file and owner of a context answer', async () => {
    const long = 'x'.repeat(5000)
    render(<StepCard step={step({ kind: 'context', tool: 'lookup_term', main_input: 'deductible', input: { term: 'deductible' },
      output: JSON.stringify({ answer: long }), sources: [{ file: 'glossary.yaml', owner: 'Claims Operations' }] })} />)
    expect(screen.queryByText('Asked')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /lookup_term/ }))
    expect(screen.getByText('Asked')).toBeInTheDocument()
    expect(screen.getByText(/"term": "deductible"/)).toBeInTheDocument()
    expect(screen.getByText('Came back')).toBeInTheDocument()
    expect(screen.getByText(/more characters/)).toBeInTheDocument() // trimmed to a readable length
    expect(screen.getByText('glossary.yaml')).toBeInTheDocument()
    expect(screen.getByText(/owned by Claims Operations/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /lookup_term/ }))
    expect(screen.queryByText('Asked')).not.toBeInTheDocument()
  })

  it('makes a card for an action stand out from a lookup', () => {
    const { container, rerender } = render(<StepCard step={step({ kind: 'action', tool: 'send_back', main_input: 'CLM-1003' })} />)
    expect(container.querySelector('.cp-step--action')).not.toBeNull()
    rerender(<StepCard step={step()} />)
    expect(container.querySelector('.cp-step--action')).toBeNull()
  })

  it('gives each kind its own icon', () => {
    const kinds = ['claims', 'customers', 'context', 'work', 'memory', 'action'] as const
    const shapes = kinds.map((kind) => {
      const { container, unmount } = render(<StepCard step={step({ kind })} />)
      const first = container.querySelector('.cp-step-head .cp-icon')!.innerHTML
      unmount()
      return first
    })
    expect(new Set(shapes).size).toBe(kinds.length)
  })
})
