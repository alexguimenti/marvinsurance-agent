const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })

export const formatUsd = (amount: number) => usd.format(amount)

export const STATUS_LABEL: Record<string, string> = {
  waiting: 'Waiting',
  in_progress: 'In progress',
  awaiting_approval: 'Awaiting approval',
  decided: 'Decided',
  needs_handler: 'Needs a handler',
}

export const RUN_LABEL: Record<string, string> = {
  running: 'Running',
  finished: 'Finished',
  stopped: 'Stopped',
}

/** Claim agents working at once: 1 to 25, 3 to start with. */
export const MIN_WORKERS = 1
export const MAX_WORKERS = 25
export const DEFAULT_WORKERS = 3

/** Seconds of agent time, for people. */
export const formatSeconds = (s: number) => (s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`)

export const DOCUMENT_LABEL: Record<string, string> = {
  policy_wording: 'Policy wording',
  claims_manual: 'Claims manual',
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
 * The stamp for a claim. An approval is only the agent's opinion until it is paid, so it must not
 * read APPROVE while the payment is held for a handler, or signed off and still to be made.
 */
export function stampFor(status: string, decision: 'approve' | 'decline' | 'refer'): { key: string; label: string } {
  if ((status === 'awaiting_approval' || status === 'in_progress') && decision === 'approve') {
    return { key: 'pending', label: 'Pending sign-off' }
  }
  return { key: decision, label: DECISION_LABEL[decision] }
}

/** A date and time from the API, for people. */
export const formatTime = (iso: string) =>
  new Date(iso).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' })

/** Why a run did not start. */
export function runRefused(status: number | undefined, otherwise: string): string {
  if (status === 409) return 'A dispatch or evaluation is already running.'
  if (status === 503) return 'The agent is not running. Start it with npm run dev.'
  return otherwise
}

/** A share from 0 to 1 as a whole percentage. */
export const formatShare = (share: number | null) => (share === null ? '—' : `${Math.round(share * 100)}%`)

export const SET_LABEL: Record<string, string> = {
  evaluation: 'Evaluation set',
  held_out: 'Held-out set',
}

/** "Held-out set, tiny" or "Evaluation set". */
export const describeSet = (evalSet: string | null | undefined, tiny: boolean | null | undefined) =>
  `${SET_LABEL[evalSet ?? ''] ?? evalSet}${tiny ? ', tiny' : ''}`
