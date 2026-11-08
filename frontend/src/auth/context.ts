import { createContext, useContext } from 'react'
import type { Me } from '../api'

export interface AuthValue {
  handler: Me
  signOut: () => Promise<void>
}

export const AuthContext = createContext<AuthValue | null>(null)

/** The signed-in handler. Only available inside AuthProvider, which shows the sign-in until there is one. */
export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth needs an AuthProvider')
  return value
}
