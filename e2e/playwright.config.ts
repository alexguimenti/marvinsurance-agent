import { defineConfig } from '@playwright/test'
import { resolve } from 'node:path'

const DB = 'e2e.db'
// own ports, so a dev server on 8000/5173 never collides with the e2e run
const API_PORT = 8100
const WEB_PORT = 5273
const AGENT_PORT = 8003 // a stand-in for the agent's service: never the one `npm run dev` starts
const AUTH_PORT = 8004 // a stand-in for Supabase Auth: the tests never reach the real project
const AUTH_URL = `http://127.0.0.1:${AUTH_PORT}`
const JUDGE_PORT = 8005 // a stand-in for the judge's endpoint: the tests never call the real one
const DB_PATH = `${process.cwd()}/.tmp/${DB}`
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
  use: { baseURL: `http://127.0.0.1:${WEB_PORT}`, trace: 'retain-on-failure' },
  webServer: [
    {
      command: `"${PYTHON}" -m uvicorn stand_in_auth:app --app-dir ../e2e --port ${AUTH_PORT}`,
      cwd: '../backend',
      url: `${AUTH_URL}/auth/v1/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `"${PYTHON}" -m uvicorn stand_in_judge:app --app-dir ../e2e --port ${JUDGE_PORT}`,
      cwd: '../backend',
      url: `http://127.0.0.1:${JUDGE_PORT}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      // scripted model: no model calls; fresh throwaway database (see scripts/clean.mjs)
      command: `"${PYTHON}" -m uvicorn stand_in_agent:app --app-dir ../e2e --port ${AGENT_PORT}`,
      cwd: '../backend',
      env: {
        AGENT_PROVIDER: 'scripted', DB_PATH, PYTHONPATH: '.',
        // the service grades an evaluation as it finishes it: with a key and the stand-in's address
        OPENROUTER_API_KEY: 'e2e-key', OPENROUTER_DECISIONS_URL: `http://127.0.0.1:${JUDGE_PORT}`,
      },
      url: `http://127.0.0.1:${AGENT_PORT}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `"${PYTHON}" -m uvicorn app.main:app --port ${API_PORT}`,
      cwd: '../backend',
      env: {
        AGENT_PROVIDER: 'scripted', DB_PATH, UNDERCOVER_AGENT: `http://127.0.0.1:${AGENT_PORT}`,
        VITE_SUPABASE_URL: AUTH_URL,
      },
      url: `http://127.0.0.1:${API_PORT}/claims`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${WEB_PORT} --strictPort`,
      env: {
        API_TARGET: `http://127.0.0.1:${API_PORT}`,
        VITE_SUPABASE_URL: AUTH_URL, VITE_SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_e2e',
      },
      cwd: '../frontend',
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
})
