import { NavLink, Route, Routes } from 'react-router-dom'
import Approvals from './pages/Approvals'
import ClaimDetail from './pages/ClaimDetail'
import Instructions from './pages/Instructions'
import Queue from './pages/Queue'

export default function App() {
  return (
    <>
      <header className="nav">
        <span className="logo">Marvin FartInsurance</span>
        <nav>
          <NavLink to="/" end>
            Claims
          </NavLink>
          <NavLink to="/approvals">Approvals</NavLink>
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
