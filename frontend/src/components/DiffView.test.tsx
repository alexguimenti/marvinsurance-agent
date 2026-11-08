import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { DiffLine } from '../api'
import DiffView from './DiffView'

const line = (kind: DiffLine['kind'], text: string): DiffLine => ({ kind, text })

describe('DiffView', () => {
  it('colors additions and removals and marks them with + and −', () => {
    render(<DiffView lines={[line('same', 'keep'), line('remove', 'old rule'), line('add', 'new rule')]} />)
    const removed = screen.getByText('old rule').closest('.diff-line')!
    const added = screen.getByText('new rule').closest('.diff-line')!
    expect(removed).toHaveClass('diff-remove')
    expect(added).toHaveClass('diff-add')
    expect(removed).toHaveTextContent('−')
    expect(added).toHaveTextContent('+')
    expect(screen.getByText('keep').closest('.diff-line')).toHaveClass('diff-same')
  })

  it('folds a long run of unchanged lines and says how many', () => {
    const same = Array.from({ length: 20 }, (_, i) => line('same', `unchanged ${i}`))
    render(<DiffView lines={[...same, line('add', 'the change')]} />)
    expect(screen.getByText('… 16 unchanged lines')).toBeInTheDocument()
    expect(screen.getByText('unchanged 0')).toBeInTheDocument()
    expect(screen.getByText('unchanged 19')).toBeInTheDocument()
    expect(screen.queryByText('unchanged 10')).not.toBeInTheDocument()
  })

  it('keeps a short run of unchanged lines whole', () => {
    render(<DiffView lines={[line('same', 'a'), line('same', 'b'), line('add', 'c')]} />)
    expect(screen.queryByText(/unchanged lines/)).not.toBeInTheDocument()
  })

  it('shows the model and reasoning effort before and after', () => {
    render(<DiffView lines={[]} model={{ before: 'a/model', after: 'b/model' }} reasoningEffort={{ before: 'low', after: 'medium' }} />)
    expect(screen.getByText(/a\/model/)).toBeInTheDocument()
    expect(screen.getByText(/b\/model/)).toBeInTheDocument()
    expect(screen.getByText(/low/)).toBeInTheDocument()
    expect(screen.getByText(/medium/)).toBeInTheDocument()
  })

  it('says when nothing changed', () => {
    render(<DiffView lines={[line('same', 'a')]} />)
    expect(screen.getByText(/No lines changed/)).toBeInTheDocument()
  })
})
