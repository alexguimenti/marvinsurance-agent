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
  copy_of?: string | null // set on an evaluation's copy: the claim it copies
  evaluation_id?: number | null
}

export interface ReadSection {
  document: 'policy_wording' | 'claims_manual'
  section: number
  title: string
}

export interface Run {
  run_id: number
  kind: 'dispatch' | 'evaluation'
  status: 'running' | 'finished' | 'stopped'
  workers: number
  model: string
  reasoning_effort: string
  eval_set?: 'evaluation' | 'held_out' | null
  tiny?: boolean | null
  error: string | null
  started_at: string
  finished_at: string | null
}

/** One claim of a run, and where it stands. */
export interface RunClaim {
  claim_id: string
  position: number
  status: Status
  attempts: number
  decision: 'approve' | 'decline' | 'refer' | null
  payout: number | null
  payment: Payment['status'] | null
  started_at: string | null
  finished_at: string | null
  cost_usd: number | null
  seconds: number
}

/** What a run came to, from the records. */
export interface RunSummary {
  approved: number
  declined: number
  referred: number
  awaiting_sign_off: { claim_id: string; payout: number }[]
  left_for_handler: string[]
  in_progress: string[]
}

export interface LatestDispatch {
  run: Run | null
  claims: RunClaim[]
  totals: (Usage & { seconds: number }) | null
  summary: RunSummary | null
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

/** The signed-in handler, as the backend names them from the sign-in's email. */
export interface Me {
  handler_id: string
  name: string
  role: string
  email: string
  can_approve_over_limit: boolean
}

const BASE = '/api'

interface Auth {
  getToken: () => Promise<string | null>
  onUnauthorized: () => void
}

let auth: Auth | null = null

/** How requests prove who is signed in. The AuthProvider sets this up, and a 401 from the backend ends the session. */
export function setAuth(next: Auth | null) {
  auth = next
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  const token = await auth?.getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(`${BASE}${path}`, { ...init, headers })
  if (res.status === 401) auth?.onUnauthorized()
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
export const startDispatch = (workers: number) => request<{ status: string }>('/dispatches', post({ workers }))
export const getLatestDispatch = () => request<LatestDispatch>('/dispatches/latest')
export const listApprovals = () => request<Approval[]>('/approvals')
export const getMe = () => request<Me>('/me')
export const approvePayment = (id: string) => request<Payment>(`/payments/${id}/approve`, post())

/** The scoreboard's measures for one evaluation. Shares are 0 to 1; none when there were no claims. */
export interface Measures {
  claims: number
  decision_accuracy: number | null
  accuracy: number | null
  mistake_cost: number
  straight_through: number | null
  cost_per_claim: number | null
  seconds_per_claim: number | null
  total_seconds: number
}

export interface EvaluationSummary {
  run: Run
  measures: Measures
}

export interface ScoredClaim {
  claim_id: string
  copy_of: string
  status: Status
  agent: { decision: 'approve' | 'decline' | 'refer'; payout: number; rationale: string } | null
  handler: { decision: 'approve' | 'decline' | 'refer'; payout: number }
  decision_ok: boolean
  accurate: boolean
  mistake_cost: number
  referred: boolean
  cost_usd: number | null
  seconds: number
}

export interface EvaluationReport extends EvaluationSummary {
  claims: ScoredClaim[]
}

export type EvalSet = 'evaluation' | 'held_out'

export const listEvaluations = () => request<EvaluationSummary[]>('/evaluations')
export const getEvaluation = (id: string) => request<EvaluationReport>(`/evaluations/${id}`)
export const startEvaluation = (evalSet: EvalSet, tiny: boolean, workers: number) =>
  request<{ status: string }>('/evaluations', post({ eval_set: evalSet, tiny, workers }))

/** One setting as it stands, with who last changed it. changed_by_name is null for the starting values. */
export interface SettingRow {
  name: string
  value: unknown
  changed_by: string
  changed_by_name: string | null
  changed_at: string
}

export interface ModelAllowed {
  reasoning_efforts: string[]
  price_in: number
  price_out: number
}

export interface PlatformSettings {
  settings: SettingRow[]
  models_allowed: Record<string, ModelAllowed>
}

export const getSettings = () => request<PlatformSettings>('/settings')
export const changeSetting = (name: string, value: unknown) =>
  request<{ name: string; value: unknown }>(`/settings/${name}`, { ...post({ value }), method: 'PUT' })

/** One version of the claim agent's configuration, or of the improver's recipe, with who proposed and approved it. */
export interface VersionRow {
  version: number
  summary: string
  reason: string
  proposed_by: string
  approved_by: string
  approved_by_name: string | null
  approved_at: string | null
  round_id: number | null
  created_at: string
  instructions?: string
  recipe?: string
  model?: string
  reasoning_effort?: string
  scores?: Record<string, { accuracy: number | null; grade: number | null; runs: number } | undefined>
}

export interface ConfigurationData {
  current: VersionRow & { instructions: string; model: string; reasoning_effort: string }
  current_recipe: number
  versions: (VersionRow & { model: string; reasoning_effort: string })[]
  recipes: VersionRow[]
}

export interface DiffLine {
  kind: 'same' | 'add' | 'remove'
  text: string
}

export interface VersionDiff {
  kind: 'config' | 'recipe'
  version: number
  against: number | null
  summary: string
  lines: DiffLine[]
  model?: { before: string | null; after: string | null }
  reasoning_effort?: { before: string | null; after: string | null }
}

export const getConfiguration = () => request<ConfigurationData>('/configuration')
export const getVersionDiff = (kind: 'config' | 'recipe', version: number) => request<VersionDiff>(`/configuration/${kind}/${version}/diff`)
