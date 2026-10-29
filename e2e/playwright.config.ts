import { defineConfig } from '@playwright/test'
import { resolve } from 'node:path'

const DB = 'e2e.db'
const PYTHON = resolve(
  '../backend/.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
)

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  reporter: [['list']],
  use: { baseURL: 'http://127.0.0.1:5173', trace: 'retain-on-failure' },
  webServer: [
    {
      // scripted agent: no model calls; fresh throwaway database (see scripts/clean.mjs)
      command: `"${PYTHON}" -m uvicorn app.main:app --port 8000`,
      cwd: '../backend',
      env: { AGENT_PROVIDER: 'scripted', DB_PATH: `${process.cwd()}/.tmp/${DB}` },
      url: 'http://127.0.0.1:8000/claims',
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 5173 --strictPort',
      cwd: '../frontend',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
})
