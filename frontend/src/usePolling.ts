import { useEffect } from 'react'

/** Run `load` now and then every `intervalMs` while the component is mounted; once only when the interval is null. */
export function usePolling(load: () => unknown, intervalMs: number | null) {
  useEffect(() => {
    void load()
    if (intervalMs === null) return
    const timer = setInterval(() => void load(), intervalMs)
    return () => clearInterval(timer)
  }, [load, intervalMs])
}
