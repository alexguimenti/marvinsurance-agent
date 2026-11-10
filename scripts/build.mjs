// Build the dashboard into static files: `npm run build` from the repo root. The files go to dashboard/dist, which the
// backend serves at `/`, with the Supabase address and publishable key built in. They come from .env when there is one,
// and otherwise from the environment, so the build also works on a server with no .env.
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const envFile = resolve(root, '.env')
if (existsSync(envFile)) process.loadEnvFile(envFile) // what is already in the environment wins

const missing = ['VITE_SUPABASE_URL', 'VITE_SUPABASE_PUBLISHABLE_KEY'].filter((name) => !process.env[name])
if (missing.length) {
  console.error(`The dashboard needs ${missing.join(' and ')}: put them in .env or in the environment.`)
  process.exit(1)
}

const windows = process.platform === 'win32'
const result = spawnSync('npx', ['vite', 'build', '--outDir', '../dashboard/dist', '--emptyOutDir'], {
  cwd: resolve(root, 'frontend'),
  stdio: 'inherit',
  shell: windows,
})
process.exit(result.status ?? 1)
