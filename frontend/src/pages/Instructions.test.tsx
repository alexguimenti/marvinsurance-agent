import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { mockApi, renderAt } from '../testUtils'
import Instructions from './Instructions'

describe('Instructions', () => {
  it('renders the text from the API', async () => {
    mockApi({ '/api/instructions': { text: 'You are the Farting Agent.\nApply only the policy wording.' } })
    renderAt(<Instructions />, '/instructions')
    expect(await screen.findByText(/You are the Farting Agent/)).toBeInTheDocument()
    expect(screen.getByText(/Apply only the policy wording/)).toBeInTheDocument()
  })
})
