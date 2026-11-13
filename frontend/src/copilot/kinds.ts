import type { StepKind } from './api'

export const KIND_LABEL: Record<StepKind, string> = {
  claims: 'Claims',
  customers: 'Customers and policies',
  context: 'Context layer',
  work: "The agent's work",
  memory: 'Memory',
  action: 'Changes something',
}
