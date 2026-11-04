export type Status = 'waiting' | 'in_progress' | 'awaiting_approval' | 'decided' | 'needs_handler'

export interface ClaimSummary {
  claim_id: string
  claim_type: string
  summary: string
  customer_name: string
  amount_claimed: number
  status: Status
  decision: 'approve' | 'decline' | 'refer' | null
  payout: number | null
  attempts: number
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
  manual_version?: string | null
  wording_version?: string | null
}

export interface Payment {
  payment_id: string
  claim_id: string
  amount: number
  status: 'paid' | 'held' | 'signed_off'
  approved_by: string | null
  approved_by_name?: string | null
  approved_at?: string | null
  paid_at?: string | null
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
  usage: Usage | null
  attempts: number
  last_error: string | null
  read: ReadSection[]
}

export interface ReadSection {
  document: 'policy_wording' | 'claims_manual'
  section: number
  title: string
}

export interface Run {
  run_id: number
  status: 'running' | 'finished' | 'failed'
  alive: boolean // false for a running run whose process has gone: the next run finishes it
  cap: number | null
  model: string
  summary: string | null
  error: string | null
  usage: Usage | null
  started_at: string
  finished_at: string | null
}

export interface TodoItem {
  item_id: number
  position: number
  title: string
  status: 'todo' | 'in_progress' | 'done' | 'left_for_handler'
  claim_id: string | null
  note: string | null
}

export interface LatestRun {
  run: Run | null
  todo: TodoItem[]
}

export interface Usage {
  calls: number
  input_tokens: number
  output_tokens: number
  reasoning_tokens: number
  cost_usd: number | null
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
export const startRun = (what: { limit: number } | { claimIds: string[] }) =>
  'claimIds' in what
    ? request<{ run_id: number }>('/runs/selected', post({ claim_ids: what.claimIds }))
    : request<{ run_id: number }>('/runs', post({ limit: what.limit }))
export const getLatestRun = () => request<LatestRun>('/runs/latest')
export const getInstructions = () => request<{ run: string; claim: string }>('/instructions')
export const listApprovals = () => request<Approval[]>('/approvals')
export const listHandlers = () => request<Handler[]>('/handlers')
export const approvePayment = (id: string, handlerId: string) =>
  request<Payment>(`/payments/${id}/approve`, post({ handler_id: handlerId }))
