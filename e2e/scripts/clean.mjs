// Start every e2e run from an empty database.
import { mkdirSync, rmSync } from 'node:fs'

mkdirSync('.tmp', { recursive: true })
for (const suffix of ['', '-wal', '-shm']) rmSync(`.tmp/e2e.db${suffix}`, { force: true })
