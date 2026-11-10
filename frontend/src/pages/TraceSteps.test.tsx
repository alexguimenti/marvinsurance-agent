import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { mockApi } from '../testUtils'
import Trace from './Trace'

const source = { file: 'glossary.yaml', system: 'The policy wording, version 2026.1', owner: 'Legal', reviewed: '2026-09-01' }

async function show(content: Record<string, unknown>, kind: string) {
  mockApi({ '/api/claims/CLM-1/trace': [{ seq: 1, kind, content, created_at: '' }] })
  const view = render(<Trace claimId="CLM-1" status="decided" />)
  await screen.findByRole('listitem')
  return view
}

describe("Trace: the steps of the agent's work", () => {
  it('a lookup says what was looked up, what came back and where it came from', async () => {
    await show({ tool: 'lookup_term', args: { term: 'Carrier' }, sources: [source], found: 'An airline, railway, ferry...' }, 'lookup')
    expect(screen.getByText(/Looked up the term "Carrier"/)).toBeInTheDocument()
    expect(screen.getByText(/An airline, railway, ferry/)).toBeInTheDocument()
    expect(screen.getByText('glossary.yaml · The policy wording, version 2026.1 · Legal · 2026-09-01')).toBeInTheDocument()
  })

  it("a lookup on the Gateway keeps the Gateway's name for the tool and still says what it looked up", async () => {
    await show({ tool: 'undercover-context___read_section', args: { document: 'claims_manual', section: 14 }, sources: [source], found: 'Records' }, 'lookup')
    expect(screen.getByText(/Looked up claims manual, section 14/)).toBeInTheDocument()
    expect(screen.getByText(/undercover-context___read_section/)).toBeInTheDocument()
  })

  it('a memory search shows the query and what it found', async () => {
    await show({ query: 'earlier claims', found: ['Oliver lost a passport in Nairobi.'] }, 'memory_search')
    expect(screen.getByText(/Searched memory for "earlier claims"/)).toBeInTheDocument()
    expect(screen.getByText(/Oliver lost a passport in Nairobi/)).toBeInTheDocument()
  })

  it('a memory search that finds nothing says so', async () => {
    await show({ query: 'anything', found: [] }, 'memory_search')
    expect(screen.getByText(/nothing found/i)).toBeInTheDocument()
  })

  it('a save to memory shows what was saved, and a failed one the error', async () => {
    const view = await show({ saved: ['The claim: a passport lost in Nairobi', 'The decision: approve $243.00'] }, 'memory_save')
    expect(screen.getByText(/Saved to memory/)).toBeInTheDocument()
    expect(screen.getByText(/The decision: approve \$243\.00/)).toBeInTheDocument()
    view.unmount()
    await show({ error: 'AgentCore could not be reached' }, 'memory_save')
    expect(screen.getByText(/AgentCore could not be reached/)).toBeInTheDocument()
  })

  it.each([
    ['gateway', 'Refused by the Gateway'],
    ['app', 'Refused by the app'],
    ['context', 'Refused by the context layer'],
    ['memory', 'Refused by memory'],
  ])('a refusal by %s stands out, with its reason and the tool', async (by, label) => {
    const view = await show({ by, tool: 'undercover-app___pay_claim', reason: 'Tool Execution Denied' }, 'refusal')
    expect(screen.getByText(label)).toBeInTheDocument()
    expect(screen.getByText(/Tool Execution Denied/)).toBeInTheDocument()
    expect(view.container.querySelector('li.trace-refusal')).not.toBeNull()
  })

  it('shows a tool call under the name it was called by', async () => {
    await show({ name: 'undercover-app___get_claim', args: {} }, 'tool_call')
    expect(screen.getByText(/undercover-app___get_claim/)).toBeInTheDocument()
  })
})
