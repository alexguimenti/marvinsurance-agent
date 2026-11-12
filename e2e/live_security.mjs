// The live checks of docs/PLAN_SECURE.md, as Dana Whitfield through the dashboard on 5173. Reads the password from .env in
// this process only.   node live_security.mjs <dispatch|refuse|evaluate|signoff|queue|show:CLAIM|signoff:CLAIM|sendback:CLAIM> [screens dir]
// NOTE is the send back's note (sendback:CLAIM).
// WORKERS sets the dispatch's claim agents at once (5 by default); DASHBOARD the address (http://127.0.0.1:5173).
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
  await page.getByLabel('Claim agents at once').fill(process.env.WORKERS ?? '5')
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
  const claim = step.slice(5)
  await page.goto(`${base}/claims/${claim}`)
  await page.getByText(/Case file/).waitFor()
  await page.getByRole('heading', { name: 'Agent activity' }).waitFor()
  await page.waitForTimeout(1500)
  const text = async (selector) => (await page.locator(selector).first().innerText().catch(() => '(none)')).replace(/\s+/g, ' ')
  log(`${claim} decision panel: ` + (await text('.verdict')))
  const context = page.locator('.card', { has: page.getByRole('heading', { name: 'Context used' }) })
  log(`${claim} context used: ` + ((await context.count()) ? (await context.first().innerText()).replace(/\s+/g, ' ') : '(none)'))
  const steps = await page.locator('ol.trace > li').allInnerTexts()
  log(`${claim} steps: ${steps.length}`)
  steps.forEach((t, i) => log(`  ${i + 1}. ` + t.replace(/\s+/g, ' ').slice(0, 260)))
  await page.screenshot({ path: `${shots}/${claim}.png`, fullPage: true })
} else if (step === 'refuse') {
  // a second start while one is going: the dashboard's button is off, and the app refuses a request that gets past it
  await page.waitForTimeout(1000)
  log('the Start dispatch button is disabled: ' + (await page.getByRole('button', { name: 'Start dispatch' }).isDisabled()))
  const answer = await page.evaluate(async () => {
    const key = Object.keys(localStorage).find((k) => k.endsWith('-auth-token'))
    const token = JSON.parse(localStorage.getItem(key)).access_token
    const r = await fetch('/api/dispatches', { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify({ workers: 3 }) })
    return { status: r.status, body: await r.text() }
  })
  log(`a second start through the API: ${answer.status} ${answer.body}`)
  await page.screenshot({ path: `${shots}/refused.png`, fullPage: true })
} else if (step.startsWith('signoff:')) {
  const claim = step.slice(8)
  await page.goto(`${base}/claims/${claim}`)
  await page.getByRole('button', { name: 'Sign off', exact: true }).click()
  await page.getByText(/Paid .* signed off by/).waitFor({ timeout: 60_000 })
  log(`${claim}: ` + (await page.getByText(/Paid .* signed off by/).first().innerText()).replace(/\s+/g, ' '))
  await page.screenshot({ path: `${shots}/${claim}-signed-off.png`, fullPage: true })
} else if (step.startsWith('sendback:')) {
  const claim = step.slice(9)
  await page.goto(`${base}/claims/${claim}`)
  await page.getByRole('button', { name: 'Send back', exact: true }).click()
  await page.getByLabel(/Note for the agent/i).fill(process.env.NOTE ?? 'Please check the amounts against the plan limits again.')
  await page.getByRole('button', { name: 'Send it back' }).click()
  // the agent decides again: the claim leaves "Sent back" and shows a decision again
  await page.waitForFunction(() => /Awaiting approval|Decided/.test(document.querySelector('main')?.innerText ?? '') && /Sent back by/.test(document.querySelector('main')?.innerText ?? ''), null, { timeout: 5 * 60_000 })
  await page.waitForTimeout(1500)
  const text = (await page.locator('main').innerText()).replace(/\s+/g, ' ')
  log(`${claim} after the send back: ` + text.slice(0, 900))
  await page.screenshot({ path: `${shots}/${claim}-sent-back.png`, fullPage: true })
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
