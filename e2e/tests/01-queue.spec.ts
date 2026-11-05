import { expect, test } from '@playwright/test'

// Specs share one backend and database, and run in file order: 00-dispatch has already worked the
// whole queue, so no claim is waiting when this file starts.
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
  await page.getByRole('table').getByRole('link', { name: 'CLM-1001' }).click() // the run panel links it too

  await expect(page.getByRole('heading', { name: 'CLM-1001' })).toBeVisible()
  await expect(page.locator('.decision-approve')).toBeVisible()
  await expect(page.locator('p', { hasText: /^The loss is covered. Section 10 of the policy wording/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Agent activity' })).toBeVisible()
  await expect(page.locator('.trace li')).not.toHaveCount(0)
  await expect(page.locator('.trace .kind', { hasText: 'tool call' }).first()).toBeVisible()
})

test('the agent instructions are visible', async ({ page }) => {
  await page.goto('/instructions')
  await expect(page.getByRole('heading', { name: 'Working a claim' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Running the queue' })).toHaveCount(0)
  await expect(page.getByText(/You are the Marvin Agent/)).toBeVisible()
})
