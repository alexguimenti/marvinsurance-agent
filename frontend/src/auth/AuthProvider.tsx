import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { getMe, setAuth, type Me } from '../api'
import { AuthContext, type AuthValue } from './context'
import { supabase } from './supabase'

type State = { status: 'loading' } | { status: 'out'; message: string | null } | { status: 'in'; handler: Me }

const SESSION_ENDED = 'Your session ended. Please sign in again.'

/** Keeps the Supabase session, shows the sign-in until there is one, and gives the app the handler the backend names. */
export default function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ status: 'loading' })

  const leave = useCallback(async (message: string | null) => {
    await supabase.auth.signOut().catch(() => {})
    setState({ status: 'out', message })
  }, [])

  const enter = useCallback(async () => {
    try {
      setState({ status: 'in', handler: await getMe() })
    } catch {
      await leave('We could not check who you are. Please sign in again.')
    }
  }, [leave])

  useEffect(() => {
    setAuth({
      getToken: async () => (await supabase.auth.getSession()).data.session?.access_token ?? null,
      onUnauthorized: () => void leave(SESSION_ENDED),
    })
    void supabase.auth.getSession().then(({ data }) => {
      if (data.session) void enter()
      else setState({ status: 'out', message: null })
    })
    const { data } = supabase.auth.onAuthStateChange((event) => {
      if (event === 'SIGNED_OUT') setState((s) => (s.status === 'out' ? s : { status: 'out', message: null }))
    })
    return () => data.subscription.unsubscribe()
  }, [enter, leave])

  const value = useMemo<AuthValue | null>(
    () => (state.status === 'in' ? { handler: state.handler, signOut: () => leave(null) } : null),
    [state, leave],
  )

  if (state.status === 'loading') return <p className="muted auth-loading">Checking your session</p>
  if (state.status === 'out') return <SignIn message={state.message} onSignedIn={enter} />
  return <AuthContext.Provider value={value!}>{children}</AuthContext.Provider>
}

function SignIn({ message, onSignedIn }: { message: string | null; onSignedIn: () => Promise<void> }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    const { error: failed } = await supabase.auth.signInWithPassword({ email, password })
    if (failed) {
      setError('The email or password is wrong. Check them and try again.')
      setBusy(false)
      return
    }
    await onSignedIn()
    setBusy(false)
  }

  return (
    <main className="signin">
      <form className="card signin-card" onSubmit={submit}>
        <header className="signin-head">
          <p className="eyebrow">MarvInsurance claims desk</p>
          <h1>Sign in</h1>
          <p className="lede">Use your work email. There is no sign-up: ask the head of claims operations for access.</p>
        </header>
        {message && <p className="notice" role="status">{message}</p>}
        {error && <p className="error signin-error" role="alert">{error}</p>}
        <div className="signin-field">
          <label htmlFor="signin-email">Email</label>
          <input id="signin-email" type="email" autoComplete="username" placeholder="name@marvinsurance.example" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div className="signin-field">
          <label htmlFor="signin-password">Password</label>
          <input id="signin-password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>
        <button className="btn btn-primary signin-submit" type="submit" disabled={busy}>{busy ? 'Signing in' : 'Sign in'}</button>
      </form>
    </main>
  )
}
