// Run every test suite, in order, stopping at the first failure: `npm run check` from the repo root.
import { spawnSync } from 'node:child_process'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
// the checks need no Supabase, Langfuse or running agent, whatever this shell has set
for (const name of ['DATABASE_URL', 'LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY', 'LANGFUSE_BASE_URL', 'UNDERCOVER_AGENT',
  'VITE_SUPABASE_URL', 'VITE_SUPABASE_PUBLISHABLE_KEY', 'HANDLER_PASSWORD', 'MODEL_NAME', 'OPENROUTER_DECISIONS_URL']) {
  delete process.env[name]
}
const python = resolve(root, 'backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')

const steps = [
  ['Backend unit tests', python, ['-m', 'pytest', '-q'], 'backend'],
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
