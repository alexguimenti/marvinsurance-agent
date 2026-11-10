// The live checks of docs/PLAN_SECURE.md, as Dana Whitfield through the dashboard on 5173. Reads the password from .env in
// this process only.   node live_security.mjs <dispatch|evaluate|signoff> [screens dir]
import { createRequire } from 'node:module'
import { mkdirSync } from 'node:fs'
const require = createRequire(import.meta.url)
const { chromium } = require('@playwright/test')
process.loadEnvFile('../.env')
const [step = 'dispatch', shots = 'shots'] = process.argv.slice(2)
mkdirSync(shots, { recursive: true })
const log = (m) => console.log(`${new Date().toISOString().slice(11, 19)} ${m}`)
const base = process.env.DASHBOARD ?? 'http://127.0.0.1:5173'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
await page.goto(`${base}/`)
await page.getByLabel('Email').fill('dana.whitfield@marvinsurance.example')
await page.getByLabel('Password').fill(process.env.HANDLER_PASSWORD)
await page.getByRole('button', { name: 'Sign in' }).click()
await page.getByRole('button', { name: 'Sign out' }).waitFor()

if (step === 'dispatch') {
  await page.getByLabel('Claim agents at once').fill('5')
  await page.getByRole('button', { name: 'Start dispatch' }).click()
  await page.locator('.run-summary').waitFor({ timeout: 20 * 60_000 })
  log('summary: ' + (await page.locator('.run-summary').innerText()).replace(/\s+/g, ' ').slice(0, 400))
  log('totals: ' + (await page.locator('.run-totals').innerText()).replace(/\s+/g, ' '))
  await page.screenshot({ path: `${shots}/queue.png`, fullPage: true })
  await page.goto(`${base}/claims/CLM-1901`)
  await page.getByText(/security check/i).first().waitFor()
  log('CLM-1901: ' + (await page.locator('main').innerText()).replace(/\s+/g, ' ').slice(0, 700))
  await page.screenshot({ path: `${shots}/clm-1901.png`, fullPage: true })
} else if (step === 'queue') {
  await page.locator('tbody tr').first().waitFor()
  log('claims shown at ' + base + ': ' + (await page.locator('tbody tr').count()))
  await page.screenshot({ path: `${shots}/queue-from-backend.png` })
} else if (step.startsWith('show:')) {
  await page.goto(`${base}/claims/${step.slice(5)}`)
  await page.getByText(/Case file/).waitFor()
  const text = (await page.locator('main').innerText()).replace(/\s+/g, ' ')
  log(text.slice(-1400))
} else if (step === 'evaluate') {
  await page.goto(`${base}/scoreboard`)
  await page.getByLabel('Set').selectOption('evaluation')
  await page.getByLabel('Only the five tiny claims').check()
  await page.getByLabel('Claim agents at once').fill('5')
  await page.getByRole('button', { name: 'Start evaluation' }).click()
  const row = page.locator('tbody tr').first()
  await row.getByText('Finished').waitFor({ timeout: 15 * 60_000 })
  log('evaluation: ' + (await row.innerText()).replace(/\s+/g, ' '))
  await row.getByRole('link').first().click()
  await page.getByRole('heading', { name: /Evaluation \d+/ }).waitFor()
  const claims = await page.locator('tbody tr').allInnerTexts()
  claims.forEach((c) => log('claim: ' + c.replace(/\s+/g, ' ').slice(0, 200)))
  await page.screenshot({ path: `${shots}/evaluation.png`, fullPage: true })
} else if (step === 'signoff') {
  await page.goto(`${base}/approvals`)
  const rows = page.locator('tbody tr')
  log('held: ' + (await rows.count()))
  const first = rows.first()
  const claim = (await first.innerText()).replace(/\s+/g, ' ')
  await first.getByRole('button', { name: 'Approve payment' }).click()
  await page.waitForTimeout(3000)
  log('after: ' + (await rows.count()) + ' held; signed off ' + claim.slice(0, 120))
  await page.goto(`${base}/claims/CLM-1003`)
  log('CLM-1003: ' + (await page.locator('main').innerText()).replace(/\s+/g, ' ').slice(0, 500))
  await page.screenshot({ path: `${shots}/signoff.png`, fullPage: true })
}
await browser.close()
