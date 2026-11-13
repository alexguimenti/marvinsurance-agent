// Run every test suite, in order, stopping at the first failure: `npm run check` from the repo root.
import { spawnSync } from 'node:child_process'
import { cpSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { relative, resolve, sep } from 'node:path'

const root = resolve(import.meta.dirname, '..')
// the checks need no Supabase, Langfuse or running agent, whatever this shell has set
for (const name of ['DATABASE_URL', 'LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY', 'LANGFUSE_BASE_URL', 'UNDERCOVER_AGENT',
  'VITE_SUPABASE_URL', 'VITE_SUPABASE_PUBLISHABLE_KEY', 'HANDLER_PASSWORD', 'MODEL_NAME', 'OPENROUTER_DECISIONS_URL', 'UNDERCOVER_API_KEY', 'UNDERCOVER_BACKEND', 'UNDERCOVER_API',
  'UNDERCOVER_GATEWAY_URL', 'UNDERCOVER_CONTEXT', 'UNDERCOVER_MEMORY_ID', 'AWS_PROFILE', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
  'AWS_SESSION_TOKEN', 'UNDERCOVER_AGENT_RUNTIME', 'UNDERCOVER_COPILOT', 'UNDERCOVER_COPILOT_RUNTIME', 'UNDERCOVER_COPILOT_MEMORY_ID']) {
  delete process.env[name]
}
for (const name of Object.keys(process.env)) {
  if (/^(AGENTCORE_)?CREDENTIAL_.+_NAME$/.test(name)) delete process.env[name] // that would make the agent think it is on Runtime
}
const python = resolve(root, 'backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')

// What the deployment packs for the agent: runtime/ and agent/ beside it, without tests, in a folder of their own (the platform
// team's stack puts runtime/'s files at the folder's top; here they sit beside agent/, which imports the same). The agent must import there with only the runtime group installed.
const staged = mkdtempSync(resolve(tmpdir(), 'undercover-runtime-'))
function stage() {
  const keep = (from) => (path) => !relative(from, path).split(sep).some((part) => part === 'tests' || part === '__pycache__')
  cpSync(resolve(root, 'runtime'), resolve(staged, 'runtime'), { recursive: true, filter: keep(resolve(root, 'runtime')) })
  cpSync(resolve(root, 'backend/agent'), resolve(staged, 'agent'), { recursive: true, filter: keep(resolve(root, 'backend/agent')) })
}
stage()
process.on('exit', () => rmSync(staged, { recursive: true, force: true }))

const steps = [
  ['Backend unit tests', python, ['-m', 'pytest', '-q'], 'backend'],
  // the backend must import with only the server group installed, in a temporary environment that leaves .venv alone
  ['Backend imports on a server', 'uv', ['run', '--project', 'backend', '--isolated', '--only-group', 'server', 'python', '-c', "__import__('backend.main')"], '.'],
  // and the agent on Runtime with only its own group, from the staged folder
  ['Agent imports on Runtime', 'uv', ['run', '--directory', staged, '--project', resolve(root, 'backend'), '--isolated', '--only-group', 'runtime', 'python', '-c', "__import__('runtime.main')"], '.'],
  // and the Copilot answers its health check through the file Runtime starts for it, from the same staged folder
  ['Copilot answers on Runtime', 'uv', ['run', '--directory', staged, '--project', resolve(root, 'backend'), '--isolated', '--only-group', 'runtime', 'python', resolve(root, 'scripts/check_copilot_health.py')], '.'],
  // and the context layer with only its own group, so what it packs for Lambda is all it needs
  ['Context layer imports on a server', 'uv', ['run', '--isolated', '--only-group', 'context', 'python', '-c', "__import__('context.server')"], 'backend'],
  ['Front-end unit tests', 'npx', ['vitest', 'run'], 'frontend'],
  ['Front-end type check', 'npx', ['tsc', '-b'], 'frontend'],
  ['Front-end lint', 'npx', ['oxlint', '--deny-warnings', 'src'], 'frontend'],
  ['End-to-end tests', 'npm', ['test'], 'e2e'],
]

for (const [name, command, args, dir] of steps) {
  console.log(`\n=== ${name} ===`)
  const result = spawnSync(command, args, { cwd: resolve(root, dir), stdio: 'inherit', shell: process.platform === 'win32' })
  if (result.status !== 0) {
    console.error(`\n${name} failed.`)
    process.exit(result.status ?? 1)
  }
}
console.log('\nAll checks passed.')
