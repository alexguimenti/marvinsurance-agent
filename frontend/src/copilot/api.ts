// The Copilot's side of the app's API: a handler's conversations, their turns and the steps the sidebar shows. A message and an answer to
// a confirmation go with a sign-in renewed first when it has less than 20 minutes left, since the Copilot works on one sign-in for a turn.
import { post, request } from '../api'

export type StepKind = 'claims' | 'customers' | 'context' | 'work' | 'memory' | 'action'
export type StepStatus = 'working' | 'done' | 'refused' | 'failed'
export type TurnStatus = 'working' | 'waiting' | 'done' | 'failed' | 'stopped'

/** A context-layer answer's file and who owns it. */
export interface StepSource {
  file: string
  owner: string
  system?: string
  reviewed?: string
}

/** One tool the Copilot called, shown as a card. */
export interface Step {
  step_id: number
  kind: StepKind
  tool: string
  main_input: string | null
  status: StepStatus
  input: Record<string, unknown> | null
  output: string | null
  sources: StepSource[] | null
  reason: string | null
  started_at: string
  seconds: number | null
}

/** What the confirmation card shows, read from the app's records. */
export interface ConfirmationCard {
  claim_id: string
  payment_id: string
  customer_name: string
  amount: number
  reason: string | null
}

export interface Turn {
  turn_id: number
  message: string
  status: TurnStatus
  answer: string | null
  error: string | null
  model_calls: number
  cost_usd: number | null
  seconds: number | null
  started_at: string
  confirmation: ConfirmationCard | null
  confirmation_answer: 'sign_off' | 'cancel' | null
  steps: Step[]
}

export interface Conversation {
  conversation_id: string
  title: string
  turns: Turn[]
}

export interface ConversationSummary {
  conversation_id: string
  title: string
  updated_at: string
}

export const createConversation = () => request<{ conversation_id: string }>('/copilot/conversations', post())
export const listConversations = () => request<ConversationSummary[]>('/copilot/conversations')
export const getConversation = (id: string) => request<Conversation>(`/copilot/conversations/${id}`)
export const sendMessage = (id: string, text: string) =>
  request<{ turn_id: number }>(`/copilot/conversations/${id}/messages`, post({ text }), true)
export const answerConfirmation = (id: string, answer: 'sign_off' | 'cancel') =>
  request<{ turn_id: number }>(`/copilot/conversations/${id}/confirmation`, post({ answer }), true)
