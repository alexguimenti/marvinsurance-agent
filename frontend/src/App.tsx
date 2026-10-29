import { useEffect, useState } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import { listApprovals } from './api'
import Approvals from './pages/Approvals'
import ClaimDetail from './pages/ClaimDetail'
import Instructions from './pages/Instructions'
import Queue from './pages/Queue'

const POLL_MS = 3000

/** How many payouts are held for a handler, so the nav can flag work waiting for a person. */
function useHeldCount() {
  const [count, setCount] = useState(0)
  useEffect(() => {
    let alive = true
    const load = () =>
      listApprovals()
        .then((a) => alive && setCount(a.length))
        .catch(() => {})
    void load()
    const timer = setInterval(load, POLL_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])
  return count
}

export default function App() {
  const held = useHeldCount()
  return (
    <>
      <header className="nav">
        <span className="logo">
          <span className="mark" aria-hidden="true" />
          Marvin FartInsurance
        </span>
        <nav>
          <NavLink to="/" end>
            Claims
          </NavLink>
          <NavLink to="/approvals">
            Approvals
            {held > 0 && (
              <span className="count" aria-label={`${held} waiting for approval`}>
                {held}
              </span>
            )}
          </NavLink>
          <NavLink to="/instructions">Agent instructions</NavLink>
        </nav>
      </header>
      <main className="container">
        <Routes>
          <Route path="/" element={<Queue />} />
          <Route path="/claims/:id" element={<ClaimDetail />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/instructions" element={<Instructions />} />
        </Routes>
      </main>
    </>
  )
}
