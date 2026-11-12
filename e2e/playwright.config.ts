import { defineConfig } from '@playwright/test'
import { resolve } from 'node:path'

const DB = 'e2e.db'
// own ports, so a dev server on 8000/5173 never collides with the e2e run
const API_PORT = 8100
const WEB_PORT = 5273
const AGENT_PORT = 8003 // the agent on the scripted model: never the one `npm run dev` starts
const CONTEXT_PORT = 8016 // the context layer, the real server: it only reads files
const AUTH_PORT = 8004 // a stand-in for Supabase Auth: the tests never reach the real project
const AUTH_URL = `http://127.0.0.1:${AUTH_PORT}`
const JUDGE_PORT = 8005 // a stand-in for the judge's endpoint: the tests never call the real one
const DB_PATH = `${process.cwd()}/.tmp/${DB}`
// the agent's key: the backend and the stand-in service share it, and nothing else does
const API_KEY = 'e2e-api-key-0123456789abcdef0123456789abcdef'
const API_URL = `http://127.0.0.1:${API_PORT}`
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
      // the context layer the agent's tools read: the real server, since it reads nothing but its own files
      command: `"${PYTHON}" -m context`,
      cwd: '../backend',
      env: { UNDERCOVER_CONTEXT_PORT: String(CONTEXT_PORT) },
      port: CONTEXT_PORT,
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
      // the backend first: the agent's service records everything through it. It grades an evaluation as it finishes
      // it, so it gets a key and the judge's stand-in
      command: `"${PYTHON}" -m uvicorn app.main:app --port ${API_PORT}`,
      cwd: '../backend',
      env: {
        AGENT_PROVIDER: 'scripted', DB_PATH, UNDERCOVER_AGENT: `http://127.0.0.1:${AGENT_PORT}`, UNDERCOVER_AGENT_RUNTIME: '',
        VITE_SUPABASE_URL: AUTH_URL, UNDERCOVER_API_KEY: API_KEY,
        OPENROUTER_API_KEY: 'e2e-key', OPENROUTER_DECISIONS_URL: `http://127.0.0.1:${JUDGE_PORT}`,
      },
      url: `${API_URL}/api/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      // scripted model: no model calls; the database is the backend's (see scripts/clean.mjs)
      command: `"${PYTHON}" -m uvicorn stand_in_agent:app --app-dir ../e2e --port ${AGENT_PORT}`,
      cwd: '../backend',
      env: {
        AGENT_PROVIDER: 'scripted', DB_PATH, PYTHONPATH: '.', UNDERCOVER_API_KEY: API_KEY, UNDERCOVER_BACKEND: API_URL,
        UNDERCOVER_CONTEXT: `http://127.0.0.1:${CONTEXT_PORT}/mcp`,
      },
      url: `http://127.0.0.1:${AGENT_PORT}/ping`,
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
