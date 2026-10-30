import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { mockApi, renderAt } from '../testUtils'
import Instructions from './Instructions'

describe('Instructions', () => {
  it('shows the instructions for running the queue and for working a claim', async () => {
    mockApi({ '/api/instructions': { run: 'Hand each claim to a claim agent.', claim: 'Read the claim first.' } })
    renderAt(<Instructions />, '/instructions')
    expect(await screen.findByRole('heading', { name: 'Running the queue' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Working a claim' })).toBeInTheDocument()
    expect(screen.getByText('Hand each claim to a claim agent.')).toBeInTheDocument()
    expect(screen.getByText('Read the claim first.')).toBeInTheDocument()
  })

  it('renders the text from the API', async () => {
    mockApi({ '/api/instructions': { run: 'Plan the queue.', claim: 'You are the Farting Agent.\nApply only the policy wording.' } })
    renderAt(<Instructions />, '/instructions')
    expect(await screen.findByText(/You are the Farting Agent/)).toBeInTheDocument()
    expect(screen.getByText(/Apply only the policy wording/)).toBeInTheDocument()
  })

  it('formats headings, lists and inline code instead of showing raw markdown', async () => {
    const text = [
      'Intro line.',
      '',
      '## How to work',
      '',
      '1. Read the claim with `get_claim`.',
      '2. Apply only the wording.',
      '',
      '- `approve`: covered.',
      '- `refer`: unsure.',
    ].join('\n')
    mockApi({ '/api/instructions': { run: '', claim: text } })
    renderAt(<Instructions />, '/instructions')

    expect(await screen.findByRole('heading', { name: 'How to work' })).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(4)
    expect(screen.getByText('get_claim').tagName).toBe('CODE')
    expect(screen.queryByText(/##/)).not.toBeInTheDocument()
    expect(document.querySelector('ol')).not.toBeNull()
    expect(document.querySelector('ul')).not.toBeNull()
  })

  it('nests an indented bullet list under its numbered item', async () => {
    const text = ['1. Decide one of:', '   - `approve`: covered.', '   - `refer`: unsure.', '2. Write it down.'].join('\n')
    mockApi({ '/api/instructions': { run: '', claim: text } })
    renderAt(<Instructions />, '/instructions')
    await screen.findByText(/Decide one of/)
    expect(document.querySelectorAll('ol > li')).toHaveLength(2)
    expect(document.querySelectorAll('ol > li:first-child ul > li')).toHaveLength(2)
  })
})
