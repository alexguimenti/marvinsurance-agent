import { expect, test } from '@playwright/test'

// Specs share one backend and database and run in file order. This one runs first, on a queue
// where all 25 claims are waiting, and dispatches the whole queue on the scripted model.
test('a dispatch started on the dashboard reaches the agent service with N, and works every claim', async ({ page, request }) => {
  await page.goto('/')
  await expect(page.locator('.badge-waiting')).toHaveCount(25)
  await expect(page.getByText(/No dispatch yet/)).toBeVisible()
  await expect(page.getByLabel('Claim agents at once')).toHaveValue('3')

  await page.getByLabel('Claim agents at once').fill('5')
  await page.getByRole('button', { name: 'Start dispatch' }).click()

  await expect
    .poll(async () => (await (await request.get('http://127.0.0.1:8003/received')).json()).starts)
    .toContainEqual({ path: '/dispatches', body: { workers: 5 } })
  const summary = page.locator('.run-summary')
  await expect(summary).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText(/Dispatch 1 · Finished · 5 claim agents at once · 25 of 25 done/)).toBeVisible()
  await expect(summary).toContainText('CLM-1003') // waiting for a sign-off
  await expect(summary).toContainText('CLM-1013') // left for a handler
  await expect(page.locator('.run-totals')).toContainText('of agent time')

  await expect(page.locator('.badge-waiting')).toHaveCount(0)
  await expect(page.locator('.badge-awaiting_approval')).toHaveCount(3)
  await expect(page.locator('.badge-needs_handler')).toHaveCount(1)
  await expect(page.locator('.badge-decided')).toHaveCount(21)
  await expect(page.getByRole('button', { name: 'Start dispatch' })).toBeDisabled() // nothing left to work
})

test('a decided claim shows what the agent read', async ({ page }) => {
  await page.goto('/claims/CLM-1001')
  await expect(page.getByRole('heading', { name: 'What the agent read' })).toBeVisible()
  await expect(page.getByText('Policy wording, section 10: How we work out your payment')).toBeVisible()
  await expect(page.getByText(/Claims manual 2026\.2 · policy wording 2026\.1/)).toBeVisible()
  await expect(page.getByText('Read policy wording, section 10')).toBeVisible()
})
