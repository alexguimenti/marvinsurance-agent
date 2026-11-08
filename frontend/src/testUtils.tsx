import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'
import { AuthContext } from './auth/context'
import type { Me } from './api'

type Route = unknown | { status: number; body: unknown }

export const DANA: Me = {
  handler_id: 'HND-01', name: 'Dana Whitfield', role: 'Senior Claims Handler',
  email: 'dana.whitfield@marvinsurance.example', can_approve_over_limit: true,
}
export const MARCUS: Me = {
  handler_id: 'HND-02', name: 'Marcus Oyelaran', role: 'Claims Handler',
  email: 'marcus.oyelaran@marvinsurance.example', can_approve_over_limit: false,
}

/** Stub fetch with a map of "METHOD /path" (or "/path" for GET) to JSON bodies. */
export function mockApi(routes: Record<string, Route>) {
  const calls: { method: string; url: string; body?: unknown }[] = []
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const key = method === 'GET' ? url : `${method} ${url}`
    if (!(key in routes)) throw new Error(`unmocked request: ${key}`)
    const raw = routes[key]
    const entry = (typeof raw === 'function' ? raw() : raw) as { status?: number; body?: unknown } | null
    const wrapped = entry && typeof entry === 'object' && 'status' in entry && 'body' in entry
    const status = wrapped ? (entry!.status as number) : 200
    const body = wrapped ? entry!.body : entry
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', fetchMock)
  return { calls, fetchMock }
}

/** Wrap a tree in a signed-in handler, Dana Whitfield unless another is given. */
export function signedIn(ui: ReactElement, handler: Me = DANA) {
  return <AuthContext.Provider value={{ handler, signOut: vi.fn() }}>{ui}</AuthContext.Provider>
}

export function renderAt(ui: ReactElement, path: string, routePath = '*', handler: Me = DANA) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={routePath} element={signedIn(ui, handler)} />
      </Routes>
    </MemoryRouter>,
  )
}
