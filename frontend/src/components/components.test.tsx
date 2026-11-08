import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import ConfirmDialog from './ConfirmDialog'
import PageHeader from './PageHeader'
import { Empty, ErrorState, Loading } from './States'
import StatusPill from './StatusPill'

describe('PageHeader', () => {
  it('says what the page is, what it is for, and holds its actions', () => {
    render(<PageHeader eyebrow="Platform" title="Settings" lede="The limits the platform works to." actions={<button>Save</button>} />)
    expect(screen.getByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument()
    expect(screen.getByText('Platform')).toHaveClass('eyebrow')
    expect(screen.getByText('The limits the platform works to.')).toHaveClass('lede')
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument()
  })
})

describe('StatusPill', () => {
  it('shows a word with its color', () => {
    render(<StatusPill tone="success">Kept</StatusPill>)
    expect(screen.getByText('Kept')).toHaveClass('pill', 'pill-tone-success')
  })
})

describe('States', () => {
  it('says what is loading, so the reader knows what to wait for', () => {
    render(<Loading what="the rounds" />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading the rounds')
  })

  it('says what to do when nothing is there yet', () => {
    render(<Empty>No rounds yet. Start one.</Empty>)
    expect(screen.getByText('No rounds yet. Start one.')).toBeInTheDocument()
  })

  it('says what went wrong and offers to try again', async () => {
    const retry = vi.fn()
    render(<ErrorState onRetry={retry}>Could not load the rounds.</ErrorState>)
    expect(screen.getByRole('alert')).toHaveTextContent('Could not load the rounds.')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(retry).toHaveBeenCalled()
  })
})

function Harness({ onConfirm = () => {} }: { onConfirm?: () => void }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button onClick={() => setOpen(true)}>Open</button>
      {open && (
        <ConfirmDialog title="Change it?" confirmLabel="Change" onConfirm={onConfirm} onCancel={() => setOpen(false)}>
          <p>Are you sure?</p>
        </ConfirmDialog>
      )}
    </>
  )
}

describe('ConfirmDialog', () => {
  it('is a modal dialog named by its title, with the safe choice focused', async () => {
    render(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: 'Open' }))
    const dialog = screen.getByRole('dialog', { name: 'Change it?' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
  })

  it('cancels on Escape and gives the focus back to what opened it', async () => {
    render(<Harness />)
    const opener = screen.getByRole('button', { name: 'Open' })
    await userEvent.click(opener)
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(opener).toHaveFocus()
  })

  it('keeps focus inside while it is open', async () => {
    render(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: 'Open' }))
    await userEvent.tab()
    expect(screen.getByRole('button', { name: 'Change' })).toHaveFocus()
    await userEvent.tab()
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus() // wrapped round, not out to the page behind
    await userEvent.tab({ shift: true })
    expect(screen.getByRole('button', { name: 'Change' })).toHaveFocus()
  })

  it('confirms only when asked to', async () => {
    const confirm = vi.fn()
    render(<Harness onConfirm={confirm} />)
    await userEvent.click(screen.getByRole('button', { name: 'Open' }))
    expect(confirm).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Change' }))
    expect(confirm).toHaveBeenCalledTimes(1)
  })
})
