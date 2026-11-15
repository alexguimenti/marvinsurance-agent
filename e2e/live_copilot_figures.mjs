// Checks the figures the Copilot gave against the app's own pages: the latest evaluation's measures (the Scoreboard), the settings, and the
// payments over the limit with their sign-offs (the claims' pages). Uses the browser profile live_copilot.mjs left signed in, so it needs no password.
//   node live_copilot_figures.mjs <dana|marcus>
import { createRequire } from 'node:module'
import { mkdirSync } from 'node:fs'
const require = createRequire(import.meta.url)
const { chromium } = require('@playwright/test')
const who = process.argv[2] ?? 'dana'
const shots = process.env.SHOTS ?? 'shots/copilot'
mkdirSync(shots, { recursive: true })
const base = process.env.DASHBOARD ?? 'http://127.0.0.1:5173'
const log = (m) => console.log(m)

const context = await chromium.launchPersistentContext(`.tmp/live-copilot-profile-${who}`, { viewport: { width: 1400, height: 900 } })
const page = context.pages()[0] ?? (await context.newPage())
await page.goto(`${base}/scoreboard`)
await page.getByRole('button', { name: 'Sign out' }).waitFor()
const api = (path) =>
  page.evaluate(async (p) => {
    const key = Object.keys(localStorage).find((k) => k.endsWith('-auth-token'))
    const token = JSON.parse(localStorage.getItem(key)).access_token
    return (await fetch(`/api${p}`, { headers: { Authorization: `Bearer ${token}` } })).json()
  }, path)

const evaluations = await api('/evaluations')
log('evaluations: ' + evaluations.map((e) => `#${e.run.run_id} ${e.run.eval_set}${e.run.tiny ? ' tiny' : ''} ${e.run.status}`).join('; '))
const whole = evaluations.find((e) => e.run.eval_set === 'held_out' && !e.run.tiny)
if (whole) log(`the whole held-out set, run ${whole.run.run_id}: straight_through=${whole.measures.straight_through} accuracy=${whole.measures.accuracy} claims=${whole.measures.claims}`)
const settings = await api('/settings')
const row = (n) => settings.settings.find((s) => s.name === n)?.value
log(`settings: approval_limit=${row('approval_limit')} objectives=${JSON.stringify(row('objectives'))} agents_at_once=${row('agents_at_once')}`)
const over = await api('/payouts/over-limit')
log('payouts over the limit: ' + JSON.stringify(over))
const claims = await api('/claims')
const paid = []
for (const c of claims.filter((c) => c.status === 'decided' && c.decision === 'approve' && c.payout > row('approval_limit'))) {
  const detail = await api(`/claims/${c.claim_id}`)
  paid.push(`${c.claim_id} $${c.payout} payment=${detail.payment?.status} signed_off_by=${detail.payment?.approved_by_name ?? detail.payment?.approved_by}`)
}
log('claims approved over the limit: ' + (paid.join('; ') || 'none'))
const dispatch = await api('/dispatches/latest')
log('the dispatch: ' + JSON.stringify({ run: dispatch.run?.run_id, status: dispatch.run?.status, totals: dispatch.totals }))
if (whole) log(`the evaluation spent: agent $${(whole.measures.cost_per_claim ?? 0) * whole.measures.claims}, judge $${whole.measures.judge_cost_usd}`)
await page.screenshot({ path: `${shots}/scoreboard.png`, fullPage: true })
await context.close()
