import { useCallback, useState } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import { listApprovals } from './api'
import { useAuth } from './auth/context'
import Approvals from './pages/Approvals'
import ClaimDetail from './pages/ClaimDetail'
import Instructions from './pages/Instructions'
import Evaluation from './pages/Evaluation'
import Queue from './pages/Queue'
import Scoreboard from './pages/Scoreboard'
import { usePolling } from './usePolling'

const POLL_MS = 3000

/** How many payouts are held for a handler, so the nav can flag work waiting for a person. */
function useHeldCount() {
  const [count, setCount] = useState(0)
  const load = useCallback(
    () =>
      listApprovals()
        .then((a) => setCount(a.length))
        .catch(() => {}),
    [],
  )
  usePolling(load, POLL_MS)
  return count
}

export default function App() {
  const held = useHeldCount()
  const { handler, signOut } = useAuth()
  return (
    <>
      <header className="nav">
        <span className="logo">
          <span className="mark" aria-hidden="true" />
          MarvInsurance
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
          <NavLink to="/scoreboard">Scoreboard</NavLink>
          <NavLink to="/instructions">Agent instructions</NavLink>
        </nav>
        <div className="who">
          <span className="who-name">{handler.name}</span>
          <span className="who-role muted small">{handler.role}</span>
          <button className="btn btn-ghost" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </header>
      <main className="container">
        <Routes>
          <Route path="/" element={<Queue />} />
          <Route path="/claims/:id" element={<ClaimDetail />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/scoreboard" element={<Scoreboard />} />
          <Route path="/scoreboard/:id" element={<Evaluation />} />
          <Route path="/instructions" element={<Instructions />} />
        </Routes>
      </main>
    </>
  )
}
