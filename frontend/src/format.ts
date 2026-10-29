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
