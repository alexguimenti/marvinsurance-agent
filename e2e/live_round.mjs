// A live round, driven as Dana Whitfield through the dashboard on 5173. Reads the password from .env in this process only.
//   node live_round.mjs <rejectFirst: 1|0> <screens dir>
import { createRequire } from 'node:module'
import { mkdirSync } from 'node:fs'
const require = createRequire(import.meta.url)
const { chromium } = require('@playwright/test')
process.loadEnvFile('../.env')
const [rejectFirst = '0', shots = 'shots'] = process.argv.slice(2)
mkdirSync(shots, { recursive: true })
const log = (m) => console.log(`${new Date().toISOString().slice(11, 19)} ${m}`)

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
await page.goto('http://127.0.0.1:5173/')
await page.getByLabel('Email').fill('dana.whitfield@marvinsurance.example')
await page.getByLabel('Password').fill(process.env.HANDLER_PASSWORD)
await page.getByRole('button', { name: 'Sign in' }).click()
await page.getByRole('button', { name: 'Sign out' }).waitFor()
await page.goto('http://127.0.0.1:5173/improvement')
await page.getByRole('button', { name: 'Start round' }).click()
log('dialog: ' + (await page.getByRole('dialog').innerText()).replace(/\s+/g, ' ').slice(0, 220))
await page.screenshot({ path: `${shots}/start-dialog.png` })
await page.getByRole('dialog').getByRole('button', { name: 'Start the round' }).click()

const open = page.getByRole('region', { name: 'Open round' })
const waiting = page.getByRole('region', { name: 'Waiting for your decision' })
let rejected = rejectFirst !== '1', last = '', n = 0
const started = Date.now()
while (Date.now() - started < 25 * 60_000) {
  if (await waiting.count()) {
    const text = (await waiting.innerText()).replace(/\s+/g, ' ')
    n += 1
    await page.screenshot({ path: `${shots}/waiting-${n}.png`, fullPage: true })
    log('waiting: ' + text.slice(0, 300))
    if (!rejected) {
      rejected = true
      await waiting.getByRole('button', { name: 'Reject' }).click()
      await page.getByLabel(/reason|why/i).fill('The change adds rules for one kind of claim. Look for the failure shared by several claims, and keep the instructions as goals.')
      await page.getByRole('dialog').getByRole('button', { name: /^Reject the/ }).click()
      log('rejected with a reason')
    } else {
      await waiting.getByRole('button', { name: 'Approve' }).click()
      await page.getByRole('dialog').getByRole('button', { name: /^Approve the/ }).click()
      log('approved')
    }
    await page.waitForTimeout(3000)
    continue
  }
  if ((await open.count()) === 0 && n > 0) break
  if (await open.count()) {
    const t = (await open.innerText()).replace(/\s+/g, ' ').slice(0, 200)
    if (t !== last) { last = t; log('open: ' + t); await page.screenshot({ path: `${shots}/open-${Date.now()}.png` }) }
  }
  await page.waitForTimeout(4000)
}
await page.reload()
await page.waitForTimeout(1500)
const row = page.getByRole('table', { name: 'Rounds' }).locator('tbody tr').first()
log('first row: ' + (await row.innerText()).replace(/\s+/g, ' '))
await page.screenshot({ path: `${shots}/after.png`, fullPage: true })
await browser.close()
