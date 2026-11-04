// Reseed the database in .env: `npm run reseed` from the repo root.
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const env = resolve(root, '.env')
if (existsSync(env)) process.loadEnvFile(env)
const python = resolve(root, 'backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const result = spawnSync(python, [resolve(root, 'scripts/reset_db.py')], { cwd: root, stdio: 'inherit' })
process.exit(result.status ?? 1)
