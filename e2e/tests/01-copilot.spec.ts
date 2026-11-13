import { asHandler, expect, signIn, test } from './fixtures'

// The Copilot runs here as the real app on a scripted model: the graph, its tools, the cards and the pause are the real ones, and every call it
// makes to the backend carries the handler's own sign-in. This spec runs after 00-dispatch, so three payouts are held for a sign-off. It signs
// off the last of them (CLM-1011) and leaves the first to 02-approval.

const baseURL = 'http://127.0.0.1:5273'

async function ask(page: import('@playwright/test').Page, text: string) {
  const box = page.getByLabel('Ask the Copilot')
  await box.fill(text)
  await box.press('Enter')
}

test('the sidebar is open on every page, collapses to a slim tab and stays collapsed after a reload', async ({ page }) => {
  for (const path of ['/', '/approvals', '/scoreboard', '/settings']) {
    await page.goto(path)
    await expect(page.getByLabel('Ask the Copilot')).toBeVisible()
  }
  await expect(page.getByText('Signed in as Dana Whitfield')).toBeVisible()
  await expect(page.getByText('Hi Dana')).toBeVisible()
  await page.getByRole('button', { name: 'Collapse the Copilot' }).click()
  await expect(page.getByLabel('Ask the Copilot')).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('button', { name: 'Open the Copilot' })).toBeVisible()
  await expect(page.getByLabel('Ask the Copilot')).toHaveCount(0)
  await page.getByRole('button', { name: 'Open the Copilot' }).click()
  await expect(page.getByLabel('Ask the Copilot')).toBeVisible()
})

test('a scripted turn shows a lookup, an action, a refusal and a formatted answer', async ({ page }) => {
  await page.goto('/')
  await ask(page, 'Work the queue and tell me what needs me')
  await expect(page.locator('.cp-bubble--me')).toHaveText('Work the queue and tell me what needs me')

  const lookup = page.locator('.cp-step', { hasText: 'list_claims' })
  const action = page.locator('.cp-step', { hasText: 'start_dispatch' })
  const refusal = page.locator('.cp-step', { hasText: 'get_claim' })
  await expect(lookup).toContainText('done')
  await expect(action).toHaveClass(/cp-step--action/)
  await expect(action).toContainText('2 agents')
  await expect(lookup).not.toHaveClass(/cp-step--action/) // an action stands out from a lookup
  await expect(refusal).toContainText('refused')
  await expect(refusal).toContainText('unknown claim CLM-0000')
  await refusal.getByRole('button').click() // opens to show what was asked and what came back
  await expect(refusal.getByText('Asked')).toBeVisible()
  await expect(refusal.getByText('Came back')).toBeVisible()

  const answer = page.locator('.cp-bubble--copilot')
  await expect(answer.getByRole('table')).toBeVisible()
  await expect(answer.getByRole('listitem')).toHaveCount(3)
  await expect(answer.getByRole('link', { name: 'CLM-1001' })).toHaveAttribute('href', '/claims/CLM-1001')
  await expect(page.locator('.cp-cost')).toContainText(/4 model calls · \$[\d.]+|4 model calls/)
  await expect(page.getByLabel('Ask the Copilot')).toBeEnabled()
  await expect(page.locator('.copilot-earlier summary')).toContainText('Earlier conversations (1)')
})

test('a sign-off waits for the handler, the page beside it stays usable, and pressing the button pays', async ({ page, request }) => {
  await page.goto('/')
  await ask(page, 'Please sign off one of the held payouts')
  const card = page.getByRole('region', { name: 'Confirm the sign-off' })
  await expect(card).toBeVisible()
  await expect(card).toContainText('CLM-1011')
  await expect(card).toContainText('$1,150.00')
  await expect(page.getByLabel('Ask the Copilot')).toBeDisabled()
  await expect(page.getByLabel('Ask the Copilot')).toHaveAttribute('placeholder', 'Answer the confirmation above first')

  // nothing is paid yet, and the page beside the sidebar is fully usable
  await page.getByRole('link', { name: /^Approvals/ }).click()
  await expect(page.locator('tbody tr')).toHaveCount(3)
  await expect(card).toBeVisible()
  expect((await (await request.get('/api/approvals', { headers: await asHandler(request) })).json()).length).toBe(3)

  // a reload on the way: the confirmation is still there
  await page.reload()
  await expect(page.getByRole('region', { name: 'Confirm the sign-off' })).toBeVisible()

  await page.getByRole('region', { name: 'Confirm the sign-off' }).getByRole('button', { name: 'Sign off and pay' }).click()
  await expect(page.locator('.cp-bubble--copilot').last()).toContainText('CLM-1011 was signed off and $1,150.00 was paid', { timeout: 30_000 })
  await expect(page.locator('.nav .count')).toHaveText('2') // the page beside the sidebar shows the change as it shows any other
  await page.goto('/approvals')
  await expect(page.locator('tbody tr')).toHaveCount(2)
  await page.goto('/claims/CLM-1011')
  await expect(page.getByText(/paid \$1,150\.00, signed off by dana whitfield/i)).toBeVisible()
})

test('a handler who may not sign off is told so, and no confirmation is shown', async ({ browser }) => {
  const context = await browser.newContext({ baseURL })
  const page = await context.newPage()
  await signIn(page, 'marcus')
  await expect(page.getByText('Signed in as Marcus Oyelaran')).toBeVisible()
  await ask(page, 'Please sign off one of the held payouts')
  const card = page.locator('.cp-step', { hasText: 'sign_off' })
  await expect(card).toContainText('refused')
  await expect(card).toContainText('can sign off payouts over the limit')
  await expect(page.getByRole('region', { name: 'Confirm the sign-off' })).toHaveCount(0)
  await expect(page.locator('.cp-bubble--copilot').last()).toContainText('could not sign that off')
  await context.close()
})

test('a handler never sees another handler\'s conversation', async ({ page, browser }) => {
  await page.goto('/')
  const id = await page.evaluate(() => localStorage.getItem('copilot.conversation'))
  expect(id).toBeNull() // each test starts in a browser of its own
  const created = await page.request.post('/api/copilot/conversations', { headers: await asHandler(page.request, 'dana') })
  const conversation = (await created.json()).conversation_id as string
  const marcus = await asHandler(page.request, 'marcus')
  expect((await page.request.get(`/api/copilot/conversations/${conversation}`, { headers: marcus })).status()).toBe(404)
  expect((await page.request.post(`/api/copilot/conversations/${conversation}/messages`, { headers: marcus, data: { text: 'hi' } })).status()).toBe(404)
  void browser
})

test('on a narrow window the sidebar opens over the page instead of beside it', async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 900 })
  await page.goto('/')
  const sidebar = page.locator('aside.copilot')
  await expect(sidebar).toHaveCSS('position', 'sticky')
  await page.setViewportSize({ width: 900, height: 900 })
  await expect(sidebar).toHaveCSS('position', 'fixed')
  await expect(page.getByLabel('Ask the Copilot')).toBeVisible()
})
