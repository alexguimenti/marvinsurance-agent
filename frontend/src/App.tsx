import { NavLink, Route, Routes } from 'react-router-dom'
import ClaimDetail from './pages/ClaimDetail'
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
        </nav>
      </header>
      <main className="container">
        <Routes>
          <Route path="/" element={<Queue />} />
          <Route path="/claims/:id" element={<ClaimDetail />} />
        </Routes>
      </main>
    </>
  )
}
