import { renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { usePolling } from './usePolling'

describe('usePolling', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('loads at once, then every interval', () => {
    const load = vi.fn()
    renderHook(() => usePolling(load, 2000))
    expect(load).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(4000)
    expect(load).toHaveBeenCalledTimes(3)
  })

  it('loads once and stops when the interval is null', () => {
    const load = vi.fn()
    renderHook(() => usePolling(load, null))
    vi.advanceTimersByTime(10_000)
    expect(load).toHaveBeenCalledTimes(1)
  })

  it('stops polling once unmounted', () => {
    const load = vi.fn()
    const { unmount } = renderHook(() => usePolling(load, 1000))
    unmount()
    vi.advanceTimersByTime(5000)
    expect(load).toHaveBeenCalledTimes(1)
  })
})
