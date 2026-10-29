import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

type Route = unknown | { status: number; body: unknown }

/** Stub fetch with a map of "METHOD /path" (or "/path" for GET) to JSON bodies. */
export function mockApi(routes: Record<string, Route>) {
  const calls: { method: string; url: string; body?: unknown }[] = []
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const key = method === 'GET' ? url : `${method} ${url}`
    if (!(key in routes)) throw new Error(`unmocked request: ${key}`)
    const entry = routes[key] as { status?: number; body?: unknown } | null
    const wrapped = entry && typeof entry === 'object' && 'status' in entry && 'body' in entry
    const status = wrapped ? (entry!.status as number) : 200
    const body = wrapped ? entry!.body : entry
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', fetchMock)
  return { calls, fetchMock }
}

export function renderAt(ui: ReactElement, path: string, routePath = '*') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={routePath} element={ui} />
      </Routes>
    </MemoryRouter>,
  )
}
