import { useEffect, useState } from 'react'
import { getInstructions } from '../api'

export default function Instructions() {
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    getInstructions()
      .then((r) => setText(r.text))
      .catch(() => setError(true))
  }, [])

  return (
    <section>
      <h1>Agent instructions</h1>
      <p className="muted">The system prompt the Farting Agent works with on every claim.</p>
      {error && <p className="error">Could not load the instructions.</p>}
      {text !== null && (
        <div className="card pad">
          <pre className="instructions">{text}</pre>
        </div>
      )}
    </section>
  )
}
