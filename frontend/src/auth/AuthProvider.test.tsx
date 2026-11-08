import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { approvePayment } from '../api'
import { DANA, mockApi } from '../testUtils'
import AuthProvider from './AuthProvider'
import { useAuth } from './context'

const auth = vi.hoisted(() => ({
  getSession: vi.fn(),
  signInWithPassword: vi.fn(),
  signOut: vi.fn(),
  onAuthStateChange: vi.fn(() => ({ data: { subscription: { unsubscribe: vi.fn() } } })),
}))
vi.mock('./supabase', () => ({ supabase: { auth } }))

const SESSION = { access_token: 'token-1', user: { email: DANA.email } }

function Inside() {
  const { handler, signOut } = useAuth()
  return (
    <>
      <p>Hello {handler.name}</p>
      <button onClick={() => void signOut()}>Sign out</button>
      <button onClick={() => void approvePayment('PAY-1').catch(() => {})}>Act</button>
    </>
  )
}

beforeEach(() => {
  auth.getSession.mockReset().mockResolvedValue({ data: { session: null } })
  auth.signInWithPassword.mockReset()
  auth.signOut.mockReset().mockResolvedValue({ error: null })
})

describe('AuthProvider', () => {
  it('shows the sign-in until there is a session, and offers no sign-up', async () => {
    mockApi({})
    render(<AuthProvider><Inside /></AuthProvider>)
    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toBeInTheDocument()
    expect(screen.queryByText(/sign up|create an account|register/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Hello/)).not.toBeInTheDocument()
  })

  it('signs in with email and password, then shows the app for the handler the backend names', async () => {
    auth.signInWithPassword.mockResolvedValue({ data: { session: SESSION }, error: null })
    mockApi({ '/api/me': DANA })
    render(<AuthProvider><Inside /></AuthProvider>)
    await userEvent.type(await screen.findByLabelText('Email'), DANA.email)
    await userEvent.type(screen.getByLabelText('Password'), 'secret')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Hello Dana Whitfield')).toBeInTheDocument()
    expect(auth.signInWithPassword).toHaveBeenCalledWith({ email: DANA.email, password: 'secret' })
  })

  it('says so when the email or password is wrong', async () => {
    auth.signInWithPassword.mockResolvedValue({ data: { session: null }, error: { message: 'Invalid login credentials' } })
    mockApi({})
    render(<AuthProvider><Inside /></AuthProvider>)
    await userEvent.type(await screen.findByLabelText('Email'), 'x@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'nope')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText(/email or password is wrong/i)).toBeInTheDocument()
  })

  it('goes straight in when a session is already there', async () => {
    auth.getSession.mockResolvedValue({ data: { session: SESSION } })
    mockApi({ '/api/me': DANA })
    render(<AuthProvider><Inside /></AuthProvider>)
    expect(await screen.findByText('Hello Dana Whitfield')).toBeInTheDocument()
  })

  it('signs out', async () => {
    auth.getSession.mockResolvedValue({ data: { session: SESSION } })
    mockApi({ '/api/me': DANA })
    render(<AuthProvider><Inside /></AuthProvider>)
    await userEvent.click(await screen.findByRole('button', { name: 'Sign out' }))
    expect(auth.signOut).toHaveBeenCalled()
    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('sends the token with an action, and signs out with a message on a 401', async () => {
    auth.getSession.mockResolvedValue({ data: { session: SESSION } })
    const { fetchMock } = mockApi({
      '/api/me': DANA,
      'POST /api/payments/PAY-1/approve': { status: 401, body: { detail: 'your session is not valid: sign in again' } },
    })
    render(<AuthProvider><Inside /></AuthProvider>)
    await userEvent.click(await screen.findByRole('button', { name: 'Act' }))
    expect(await screen.findByText('Your session ended. Please sign in again.')).toBeInTheDocument()
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!
    expect(new Headers(post[1]!.headers).get('Authorization')).toBe('Bearer token-1')
    await waitFor(() => expect(auth.signOut).toHaveBeenCalled())
  })
})
