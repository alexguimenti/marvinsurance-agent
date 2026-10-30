import { expect, test } from '@playwright/test'

// Specs share one backend and database, and run in file order: 00-harness has already worked
// CLM-1001 to CLM-1003, so 22 claims are waiting when this file starts.
test('ticking claims runs only the selected ones', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.badge-waiting')).toHaveCount(22)

  await page.getByRole('checkbox', { name: 'Select CLM-1004' }).check()
  await page.getByRole('checkbox', { name: 'Select CLM-1006' }).check()
  await page.getByRole('button', { name: 'Run selected (2)' }).click()

  await expect(page.locator('.badge-waiting')).toHaveCount(20, { timeout: 30_000 })
  await expect(page.getByRole('checkbox', { name: 'Select CLM-1004' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Run queue' })).toBeEnabled()
})

test('the rest of the queue is worked; a claim failing twice is left for a handler', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('tbody tr')).toHaveCount(25)
  await expect(page.locator('.badge-waiting')).toHaveCount(20)

  await page.getByRole('button', { name: 'Run queue' }).click()

  await expect(page.locator('.badge-waiting')).toHaveCount(0, { timeout: 30_000 })
  await expect(page.locator('.badge-in_progress')).toHaveCount(0)
  await expect(page.locator('.badge-awaiting_approval')).toHaveCount(3)
  await expect(page.locator('.badge-needs_handler')).toHaveCount(1)
  await expect(page.locator('.badge-decided')).toHaveCount(21)
  await expect(page.locator('.todo-left_for_handler')).toContainText('Work CLM-1013')
})

test('the claim left for a handler explains why; the claim that failed once was decided', async ({ page }) => {
  await page.goto('/claims/CLM-1013')
  await expect(page.getByText(/Left for a handler: the agent failed 2 times/)).toBeVisible()
  await expect(page.locator('.trace-error')).toHaveCount(2)

  await page.goto('/claims/CLM-1017')
  await expect(page.locator('.badge-decided')).toBeVisible()
  await expect(page.locator('.trace-error')).toHaveCount(1)
})

test('a claim shows its decision, rationale and the agent trace', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'CLM-1001' }).click()

  await expect(page.getByRole('heading', { name: 'CLM-1001' })).toBeVisible()
  await expect(page.locator('.decision-approve')).toBeVisible()
  await expect(page.locator('p', { hasText: /^The loss is covered. Section 10 of the policy wording/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Agent activity' })).toBeVisible()
  await expect(page.locator('.trace li')).not.toHaveCount(0)
  await expect(page.locator('.trace .kind', { hasText: 'tool call' }).first()).toBeVisible()
})

test('the agent instructions are visible', async ({ page }) => {
  await page.goto('/instructions')
  await expect(page.getByRole('heading', { name: 'Running the queue' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Working a claim' })).toBeVisible()
  await expect(page.getByText(/You are the Farting Agent running the claims queue/)).toBeVisible()
})
