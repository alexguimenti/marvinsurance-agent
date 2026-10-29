export type Status = 'waiting' | 'in_progress' | 'awaiting_approval' | 'decided'

export interface ClaimSummary {
  claim_id: string
  claim_type: string
  summary: string
  customer_name: string
  amount_claimed: number
  status: Status
  decision: 'approve' | 'decline' | 'refer' | null
  payout: number | null
}

export interface CalcStep {
  step: string
  amount: number
}

export interface Decision {
  decision: 'approve' | 'decline' | 'refer'
  payout: number
  calculation: CalcStep[]
  rationale: string
  citations: string[]
  model: string
}

export interface Payment {
  payment_id: string
  claim_id: string
  amount: number
  status: 'paid' | 'held' | 'void'
  approved_by: string | null
}

export interface ClaimDetailData {
  status: Status
  claim: {
    claim_id: string
    claim_type: string
    submitted_on: string
    incident_date: string
    incident_location: string
    summary: string
    story: string
    amount_claimed: number
    items: { description: string; category: string; amount: number }[]
    documents: { name: string; type: string; content: string }[]
  }
  policy: { policy_id: string; plan: string; add_ons: string[]; travelers: string[] }
  customer: { name: string; customer_since?: string; previous_claims: { claim_id: string; summary: string; outcome: string }[] }
  decision: Decision | null
  payment: Payment | null
}

export interface TraceEvent {
  seq: number
  kind: string
  content: Record<string, unknown>
  created_at: string
}

export interface Approval {
  payment_id: string
  claim_id: string
  amount: number
  status: string
  customer_name: string
  claim_type: string
  rationale: string | null
}

export interface Handler {
  handler_id: string
  name: string
  role: string
}

const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init)
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* body was not JSON */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

const post = (body?: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
})

export const listClaims = () => request<ClaimSummary[]>('/claims')
export const getClaim = (id: string) => request<ClaimDetailData>(`/claims/${id}`)
export const getTrace = (id: string) => request<TraceEvent[]>(`/claims/${id}/trace`)
export const runClaim = (id: string) => request<{ claim_id: string }>(`/claims/${id}/run`, post())
export const runQueue = () => request<{ queued: number }>('/queue/run', post())
export const getInstructions = () => request<{ text: string }>('/instructions')
export const listApprovals = () => request<Approval[]>('/approvals')
export const listHandlers = () => request<Handler[]>('/handlers')
export const approvePayment = (id: string, handlerId: string) =>
  request<Payment>(`/payments/${id}/approve`, post({ handler_id: handlerId }))
