// The live checks of docs/COPILOT.md, as a handler through the dashboard on 5173, and the Copilot's sidebar in it. Reads the password from .env in
// this process only, so it never passes through a tool call.
//   node live_copilot.mjs <dana|marcus> <step> [<step> ...]        (SHOTS sets the screens folder, DASHBOARD the address)
// Steps, run in order in one browser:
//   ask:<message>        send a message and wait until the turn is done, or waits for a confirmation
//   confirm:sign_off     press Sign off and pay (confirm:cancel presses Cancel) and wait until the turn is done
//   latest               open the newest earlier conversation
//   theme:light|dark     switch the dashboard's theme
//   shot:<name>          a screenshot of the whole window
//   reload               reload the page (the conversation is remembered in the browser)
// The browser keeps its storage in .tmp/live-copilot-profile, so a second run picks the same conversation up.
import { createRequire } from 'node:module'
import { mkdirSync } from 'node:fs'
const require = createRequire(import.meta.url)
const { chromium } = require('@playwright/test')
process.loadEnvFile('../.env')
const [who, ...steps] = process.argv.slice(2)
const emails = { dana: 'dana.whitfield@marvinsurance.example', marcus: 'marcus.oyelaran@marvinsurance.example' }
if (!emails[who]) throw new Error('the first argument is dana or marcus')
const shots = process.env.SHOTS ?? 'shots'
mkdirSync(shots, { recursive: true })
const base = process.env.DASHBOARD ?? 'http://127.0.0.1:5173'
const log = (m) => console.log(`${new Date().toISOString().slice(11, 19)} ${m}`)
const squash = (t) => t.replace(/\s+/g, ' ').trim()

const context = await chromium.launchPersistentContext(`.tmp/live-copilot-profile-${who}`, { viewport: { width: 1400, height: 900 } })
const page = context.pages()[0] ?? (await context.newPage())
await page.goto(`${base}/`)
if (await page.getByLabel('Email').isVisible({ timeout: 3000 }).catch(() => false)) {
  await page.getByLabel('Email').fill(emails[who])
  await page.getByLabel('Password').fill(process.env.HANDLER_PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
}
await page.getByRole('button', { name: 'Sign out' }).waitFor()
if (!(await page.getByLabel('Ask the Copilot').isVisible().catch(() => false))) await page.getByRole('button', { name: 'Open the Copilot' }).click()

/** Wait until the last turn is no longer working: the box is enabled again, or a confirmation waits. */
async function settled() {
  await page.waitForFunction(() => {
    const box = document.querySelector('textarea[aria-label="Ask the Copilot"]')
    const card = document.querySelector('.cp-confirm')
    const working = document.querySelector('.cp-working')
    return !working && (card || (box && !box.disabled))
  }, null, { timeout: 25 * 60_000, polling: 1000 })
}

async function report() {
  const turn = page.locator('.cp-turn').last()
  for (const card of await turn.locator('.cp-step').all()) log('  card: ' + squash(await card.innerText()).slice(0, 220))
  if (await turn.locator('.cp-confirm').count()) log('  confirmation: ' + squash(await turn.locator('.cp-confirm').innerText()))
  for (const answer of await turn.locator('.cp-bubble--copilot').all()) log('  answer: ' + squash(await answer.innerText()))
  for (const note of await turn.locator('.cp-cost, .cp-problem, .cp-pressed').all()) log('  note: ' + squash(await note.innerText()))
}

let n = 0
for (const step of steps) {
  n += 1
  const name = step.split(':')[0]
  const arg = step.slice(name.length + 1)
  log(`${n}. ${step}`)
  if (name === 'ask') {
    const box = page.getByLabel('Ask the Copilot')
    await box.fill(arg)
    await box.press('Enter')
    await settled()
    await report()
    await page.screenshot({ path: `${shots}/copilot-${who}-${n}.png` })
  } else if (name === 'confirm') {
    await page.getByRole('button', { name: arg === 'cancel' ? 'Cancel' : 'Sign off and pay' }).click()
    await page.waitForTimeout(1500)
    await settled()
    await report()
    await page.screenshot({ path: `${shots}/copilot-${who}-${n}.png` })
  } else if (name === 'latest') {
    await page.getByText(/Earlier conversations/).click()
    await page.locator('.copilot-earlier li button').first().click()
    await page.waitForTimeout(1500)
    log('  opened: ' + squash(await page.locator('.cp-bubble--me').first().innerText()))
    log('  turns: ' + (await page.locator('.cp-turn').count()))
  } else if (name === 'theme') {
    await page.evaluate((choice) => localStorage.setItem('marvin-theme', choice), arg)
    await page.reload()
    await page.getByLabel('Ask the Copilot').waitFor()
  } else if (name === 'reload') {
    await page.reload()
    await page.getByLabel('Ask the Copilot').waitFor()
  } else if (name === 'shot') {
    await page.waitForTimeout(2000) // the page and the conversation load first
    await page.screenshot({ path: `${shots}/copilot-${who}-${arg}.png`, fullPage: false })
    log('  saved ' + `${shots}/copilot-${who}-${arg}.png`)
  } else {
    throw new Error(`unknown step ${step}`)
  }
}
await context.close()
