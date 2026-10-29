import { expect, test } from '@playwright/test'

// Specs share one backend and database, and run in file order: this one runs the queue first.
test('the queue starts as 25 waiting claims and the agent works through them', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('tbody tr')).toHaveCount(25)
  await expect(page.locator('.badge-waiting')).toHaveCount(25)

  await page.getByRole('button', { name: 'Run queue' }).click()

  await expect(page.locator('.badge-waiting')).toHaveCount(0, { timeout: 30_000 })
  await expect(page.locator('.badge-in_progress')).toHaveCount(0)
  await expect(page.locator('.badge-awaiting_approval')).toHaveCount(3)
  await expect(page.locator('.badge-decided')).toHaveCount(22)
})

test('a claim shows its decision, rationale and the agent trace', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'CLM-1001' }).click()

  await expect(page.getByRole('heading', { name: 'CLM-1001' })).toBeVisible()
  await expect(page.locator('.decision-approve')).toBeVisible()
  await expect(page.locator('p', { hasText: /^The loss is covered. Section 10 of the policy wording/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Agent activity' })).toBeVisible()
  await expect(page.locator('.trace li')).not.toHaveCount(0)
  await expect(page.locator('.trace .kind', { hasText: 'tool call' })).toBeVisible()
})

test('the agent instructions are visible', async ({ page }) => {
  await page.goto('/instructions')
  await expect(page.getByText(/You are the Farting Agent/)).toBeVisible()
})
