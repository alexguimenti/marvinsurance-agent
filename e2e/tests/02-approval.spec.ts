import { expect, test } from '@playwright/test'

test.beforeAll(async ({ request }) => {
  // make sure the queue has run, in case this file is run on its own
  await request.post('/api/queue/run')
  await expect
    .poll(async () => (await (await request.get('/api/approvals')).json()).length, { timeout: 30_000 })
    .toBe(3)
})

test('an over-limit payout stays held until a handler approves it', async ({ page }) => {
  await page.goto('/claims/CLM-1003')
  await expect(page.getByText(/held for approval/i)).toBeVisible()
  await expect(page.locator('.badge-awaiting_approval')).toBeVisible()
  // the agent said approve, but nobody has signed off yet: no APPROVE stamp
  await expect(page.getByText('Pending sign-off')).toBeVisible()
  await expect(page.locator('.decision-approve')).toHaveCount(0)

  await page.goto('/approvals')
  await expect(page.locator('tbody tr')).toHaveCount(3)
  const row = page.locator('tbody tr', { hasText: 'CLM-1003' })
  await expect(row).toContainText('$3,750.00')

  await page.getByLabel('Approve as').selectOption({ label: 'Dana Whitfield (Senior Claims Handler)' })
  await row.getByRole('button', { name: 'Approve payment' }).click()

  await expect(page.locator('tbody tr')).toHaveCount(2)
  await expect(page.locator('tbody tr', { hasText: 'CLM-1003' })).toHaveCount(0)

  await page.goto('/claims/CLM-1003')
  await expect(page.locator('.badge-decided')).toBeVisible()
  await expect(page.getByText(/held for approval/i)).toHaveCount(0)
  await expect(page.locator('.decision-approve')).toBeVisible()
  await expect(page.getByText('Pending sign-off')).toHaveCount(0)
})

test('approving the rest empties the approvals list', async ({ page }) => {
  await page.goto('/approvals')
  // wait for the list to load: counting rows right after goto() would see zero and skip the loop
  await expect(page.locator('tbody tr')).not.toHaveCount(0)
  while ((await page.locator('tbody tr').count()) > 0) {
    const before = await page.locator('tbody tr').count()
    await page.getByRole('button', { name: 'Approve payment' }).first().click()
    await expect(page.locator('tbody tr')).toHaveCount(before - 1)
  }
  await expect(page.getByText(/nothing is waiting for approval/i)).toBeVisible()
})
