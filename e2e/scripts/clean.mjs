// Start every e2e run from an empty database and no saved checkpoints.
import { mkdirSync, rmSync } from 'node:fs'

mkdirSync('.tmp', { recursive: true })
for (const name of ['e2e.db', 'e2e.checkpoints.db']) {
  for (const suffix of ['', '-wal', '-shm']) rmSync(`.tmp/${name}${suffix}`, { force: true })
}
