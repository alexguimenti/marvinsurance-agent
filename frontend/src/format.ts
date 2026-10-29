const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })

export const formatUsd = (amount: number) => usd.format(amount)

export const STATUS_LABEL: Record<string, string> = {
  waiting: 'Waiting',
  in_progress: 'In progress',
  awaiting_approval: 'Awaiting approval',
  decided: 'Decided',
}

export const DECISION_LABEL: Record<string, string> = {
  approve: 'Approve',
  decline: 'Decline',
  refer: 'Refer',
}

export const humanize = (value: string) => value.replace(/_/g, ' ')

/** Model calls cost fractions of a cent, so show four decimals below one cent. */
export const formatCost = (usd: number) => (usd === 0 ? '$0' : `$${usd.toFixed(usd >= 0.01 ? 2 : 4)}`)

const num = (n: number) => n.toLocaleString('en-US')

export function describeUsage(u: {
  calls: number
  input_tokens: number
  output_tokens: number
  reasoning_tokens: number
  cost_usd: number | null
}): string {
  const cost = u.cost_usd === null ? 'cost not reported' : formatCost(u.cost_usd)
  const reasoning = u.reasoning_tokens ? ` (${num(u.reasoning_tokens)} reasoning)` : ''
  const calls = `${u.calls} model call${u.calls === 1 ? '' : 's'}`
  return `${cost} · ${num(u.input_tokens)} tokens in · ${num(u.output_tokens)} out${reasoning} · ${calls}`
}

/**
 * The stamp for a claim. An approval above the limit is only the agent's opinion until a handler
 * signs it off, so it must not read APPROVE while the payment is still held.
 */
export function stampFor(status: string, decision: 'approve' | 'decline' | 'refer'): { key: string; label: string } {
  if (status === 'awaiting_approval' && decision === 'approve') return { key: 'pending', label: 'Pending sign-off' }
  return { key: decision, label: DECISION_LABEL[decision] }
}

