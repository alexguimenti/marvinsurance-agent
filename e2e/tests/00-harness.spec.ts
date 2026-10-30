import { expect, test } from '@playwright/test'

// Specs share one backend and database and run in file order. This one runs first, on a queue
// where all 25 claims are waiting. The scripted orchestrator takes claims in id order.
test('a run capped at 3 plans, works and ticks off three claims; the rest stay waiting', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.badge-waiting')).toHaveCount(25)
  await expect(page.getByText(/No run yet/)).toBeVisible()

  await page.getByLabel('Limit').fill('3')
  await page.getByRole('button', { name: 'Run queue' }).click()

  const items = page.locator('.todo-item')
  await expect(items).toHaveCount(3, { timeout: 30_000 })
  await expect(items.nth(0)).toContainText('Work CLM-1001')
  await expect(page.locator('.todo-done')).toHaveCount(3, { timeout: 30_000 })
  await expect(page.getByText(/Run 1 · Finished · capped at 3 · 3 of 3 done/)).toBeVisible()
  await expect(page.locator('.todo-summary')).toContainText('Worked 3 claims.')

  await expect(page.locator('.badge-waiting')).toHaveCount(22)
  await expect(page.locator('.badge-awaiting_approval')).toHaveCount(1) // CLM-1003 is held for sign-off
})

test('a decided claim shows what the agent read', async ({ page }) => {
  await page.goto('/claims/CLM-1001')
  await expect(page.getByRole('heading', { name: 'What the agent read' })).toBeVisible()
  await expect(page.getByText('Policy wording, section 10: How we work out your payment')).toBeVisible()
  await expect(page.getByText(/Claims manual 2026\.2 · policy wording 2026\.1/)).toBeVisible()
  await expect(page.getByText('Read policy wording, section 10')).toBeVisible()
})
