import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { linkClaims } from './linkClaims'
import Markdown from './Markdown'

const show = (text: string) => render(<MemoryRouter><Markdown text={text} /></MemoryRouter>)

describe('the Copilot\'s markdown', () => {
  it('renders a table, a list and links a claim id to its page', () => {
    show('Two payouts need you:\n\n- CLM-1012, $2,000\n- CLM-1003, $3,750\n\n| Claim | Status |\n|---|---|\n| CLM-1012 | held |\n')
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    const links = screen.getAllByRole('link', { name: 'CLM-1012' })
    expect(links.length).toBeGreaterThan(0)
    for (const link of links) expect(link).toHaveAttribute('href', '/claims/CLM-1012')
  })

  it('links an id the model wrote as code, and leaves other code and existing links alone', () => {
    expect(linkClaims('see `CLM-1012` now')).toBe('see [CLM-1012](/claims/CLM-1012) now')
    expect(linkClaims('run `get_claim CLM-1012`')).toBe('run `get_claim CLM-1012`')
    expect(linkClaims('[CLM-1012](/claims/CLM-1012)')).toBe('[CLM-1012](/claims/CLM-1012)')
    expect(linkClaims('NOT-CLM-1012 and CLM-10123')).toBe('NOT-CLM-1012 and CLM-10123')
  })

  it('opens other links in a new tab', () => {
    show('[the manual](https://example.com/manual)')
    expect(screen.getByRole('link', { name: 'the manual' })).toHaveAttribute('target', '_blank')
  })
})
