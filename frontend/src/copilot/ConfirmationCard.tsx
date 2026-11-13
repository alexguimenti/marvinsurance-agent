import { Link } from 'react-router-dom'
import { formatUsd } from '../format'
import type { ConfirmationCard as Card } from './api'

/** The pause before a payout: the claim, the customer, the amount it will pay and the agent's reason, with two buttons. Everything on it
 *  was read from our records by the app, never taken from what the model wrote. */
export default function ConfirmationCard({ card, onAnswer, busy }: { card: Card; onAnswer: (answer: 'sign_off' | 'cancel') => void; busy: boolean }) {
  return (
    <section className="cp-confirm" aria-label="Confirm the sign-off">
      <p className="cp-confirm-title">Sign off this payout?</p>
      <dl>
        <dt>Claim</dt>
        <dd><Link to={`/claims/${card.claim_id}`}>{card.claim_id}</Link></dd>
        <dt>Customer</dt>
        <dd>{card.customer_name}</dd>
        <dt>Will pay</dt>
        <dd className="cp-confirm-amount">{formatUsd(card.amount)}</dd>
        <dt>Agent's reason</dt>
        <dd>{card.reason ?? 'none recorded'}</dd>
      </dl>
      <div className="cp-confirm-actions">
        <button type="button" className="btn btn-accent btn-sm" disabled={busy} onClick={() => onAnswer('sign_off')}>Sign off and pay</button>
        <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => onAnswer('cancel')}>Cancel</button>
      </div>
    </section>
  )
}
