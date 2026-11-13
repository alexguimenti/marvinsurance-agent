// Start everything: `npm run dev` from the repo root. The backend, the context layer, the claim agent, the Copilot and the dashboard,
// with .env in their environment. Ctrl-C stops all of them, and if one dies the others stop too.
import { spawn, spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const envFile = resolve(root, '.env')
if (existsSync(envFile)) process.loadEnvFile(envFile)
const windows = process.platform === 'win32'
const python = resolve(root, 'backend/.venv', windows ? 'Scripts/python.exe' : 'bin/python')
const uvicorn = ['-m', 'uvicorn', '--host', '127.0.0.1', '--no-access-log']

const parts = [
  {
    name: 'backend',
    cwd: 'backend',
    command: python,
    args: [...uvicorn, 'app.main:app', '--port', '8000', '--reload', '--reload-dir', 'app'],
  },
  // no --reload: a reload would stop the claim it is working on
  // the context layer: definitions, plans, rules, reference data and documents, for the claim agent's tools
  { name: 'context', cwd: 'backend', command: python, args: ['-m', 'context'] },
  { name: 'agent', cwd: 'backend', command: python, args: [...uvicorn, 'agent.entrypoint:app', '--port', '8080', '--reload', '--reload-dir', 'agent'] },
  // the Copilot, in the sidebar: it asks for no key, since everything it does carries the handler's sign-in
  { name: 'copilot', cwd: 'backend', command: python, args: [...uvicorn, 'agent.copilot.entrypoint:app', '--port', '8081', '--reload', '--reload-dir', 'agent'] },
  { name: 'dashboard', cwd: 'frontend', command: 'npm', args: ['run', 'dev', '--', '--host', '127.0.0.1', '--strictPort'], shell: windows },
]

const running = new Map()
let stopping = false

function stopAll(code) {
  if (stopping) return
  stopping = true
  for (const child of running.values()) {
    // a process tree: uvicorn's reloader and npm both start children of their own
    if (windows) spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' })
    else child.kill('SIGTERM')
  }
  process.exitCode = code
}

function prefix(name, stream, out) {
  let rest = ''
  stream.on('data', (chunk) => {
    const lines = (rest + chunk).split(/\r?\n/)
    rest = lines.pop()
    for (const line of lines) out.write(`[${name}] ${line}\n`)
  })
}

for (const part of parts) {
  const child = spawn(part.command, part.args, { cwd: resolve(root, part.cwd), env: process.env, shell: part.shell ?? false })
  running.set(part.name, child)
  prefix(part.name, child.stdout, process.stdout)
  prefix(part.name, child.stderr, process.stderr)
  child.on('exit', (code) => {
    running.delete(part.name)
    if (!stopping) {
      console.error(`[dev] ${part.name} stopped (exit ${code}). Stopping the others.`)
      stopAll(code ?? 1)
    }
  })
}

process.on('SIGINT', () => stopAll(0))
process.on('SIGTERM', () => stopAll(0))
