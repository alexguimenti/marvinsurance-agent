import type { Turn } from './api'
import ConfirmationCard from './ConfirmationCard'
import Markdown from './Markdown'
import StepCard from './StepCard'

function costLine(turn: Turn): string {
  const parts = [`${turn.model_calls} model call${turn.model_calls === 1 ? '' : 's'}`]
  if (turn.cost_usd !== null) parts.push(`$${turn.cost_usd.toFixed(4)}`)
  if (turn.seconds !== null) parts.push(`${Math.round(turn.seconds)} s`)
  return parts.join(' · ')
}

const PRESSED = { sign_off: 'You pressed Sign off and pay.', cancel: 'You pressed Cancel.' }

/** One turn: the handler's message, each step as a card, then the working indicator, the confirmation or the answer. */
export function TurnView({ turn, onAnswer, answering }: { turn: Turn; onAnswer: (answer: 'sign_off' | 'cancel') => void; answering: boolean }) {
  return (
    <div className="cp-turn">
      <div className="cp-bubble cp-bubble--me">{turn.message}</div>
      {turn.steps.map((step) => (
        <StepCard key={step.step_id} step={step} />
      ))}
      {turn.status === 'working' && (
        <div className="cp-working" role="status" aria-label="The Copilot is working">
          <span /><span /><span />
        </div>
      )}
      {turn.confirmation && turn.status === 'waiting' && <ConfirmationCard card={turn.confirmation} onAnswer={onAnswer} busy={answering} />}
      {turn.confirmation && turn.status !== 'waiting' && turn.confirmation_answer && (
        <p className="cp-pressed small muted">{PRESSED[turn.confirmation_answer]}</p>
      )}
      {turn.status === 'done' && turn.answer && (
        <>
          <div className="cp-bubble cp-bubble--copilot"><Markdown text={turn.answer} /></div>
          <p className="cp-cost small muted">{costLine(turn)}</p>
        </>
      )}
      {turn.status === 'failed' && (
        <p className="cp-problem" role="alert">Something went wrong: {turn.error ?? 'the Copilot stopped.'} You can send a new message.</p>
      )}
      {turn.status === 'stopped' && <p className="cp-problem" role="alert">This turn stopped before it finished. You can send a new message.</p>}
    </div>
  )
}

export default function ConversationView({ turns, onAnswer, answering }: { turns: Turn[]; onAnswer: (answer: 'sign_off' | 'cancel') => void; answering: boolean }) {
  return (
    <>
      {turns.map((turn) => (
        <TurnView key={turn.turn_id} turn={turn} onAnswer={onAnswer} answering={answering} />
      ))}
    </>
  )
}
