import '@fontsource-variable/fraunces/opsz.css'
import '@fontsource-variable/geist'
import '@fontsource-variable/geist-mono'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import AuthProvider from './auth/AuthProvider'
import './styles/tokens.css'
import './styles/app.css'
import { applyTheme, followComputerTheme } from './theme'

applyTheme()
followComputerTheme()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
