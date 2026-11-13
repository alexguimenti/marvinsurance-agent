// Conversations for the sidebar's tests.
import type { Conversation, ConfirmationCard, Step, Turn } from './api'

export function step(over: Partial<Step> = {}): Step {
  return {
    step_id: 1, kind: 'claims', tool: 'get_claim', main_input: 'CLM-1012', status: 'done', input: { claim_id: 'CLM-1012' },
    output: '{"status":"decided"}', sources: null, reason: null, started_at: new Date().toISOString(), seconds: 0.4, ...over,
  }
}

export function turn(over: Partial<Turn> = {}): Turn {
  return {
    turn_id: 1, message: 'What needs me?', status: 'done', answer: 'Two payouts need you.', error: null, model_calls: 3, cost_usd: 0.0123,
    seconds: 14, started_at: new Date().toISOString(), confirmation: null, confirmation_answer: null, steps: [], ...over,
  }
}

export const CARD: ConfirmationCard = {
  claim_id: 'CLM-1003', payment_id: 'PAY-CLM-1003', customer_name: 'Maya Patel', amount: 3750, reason: 'Covered under section 10.',
}

export function conversation(turns: Turn[], id = 'c-1'): Conversation {
  return { conversation_id: id, title: turns[0]?.message ?? '', turns }
}
