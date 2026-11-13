import { asHandler, expect, test } from './fixtures'

// Three payouts are held after the dispatch, or two when the Copilot's spec has already signed off the last of them.
let held = 0

test.beforeAll(async ({ request }) => {
  // make sure the queue has run, in case this file is run on its own
  await request.post('/api/dispatches', { data: { workers: 5 }, headers: await asHandler(request) })
  await expect
    .poll(async () => (await (await request.get('/api/approvals', { headers: await asHandler(request) })).json()).length, { timeout: 30_000 })
    .toBeGreaterThanOrEqual(2)
  held = (await (await request.get('/api/approvals', { headers: await asHandler(request) })).json()).length
})

test('a sign-off on the dashboard pays the claim, with no help from the agent', async ({ page }) => {
  await page.goto('/claims/CLM-1003')
  await expect(page.getByText(/held for approval/i)).toBeVisible()
  await expect(page.locator('.badge-awaiting_approval')).toBeVisible()
  // the agent said approve, but nobody has signed off yet: no APPROVE stamp
  await expect(page.getByText('Pending sign-off')).toBeVisible()
  await expect(page.locator('.decision-approve')).toHaveCount(0)

  await page.goto('/approvals')
  await expect(page.locator('tbody tr')).toHaveCount(held)
  const row = page.locator('tbody tr', { hasText: 'CLM-1003' })
  await expect(row).toContainText('$3,750.00')

  await expect(page.getByText('Signing off as Dana Whitfield')).toBeVisible()
  await row.getByRole('button', { name: 'Approve payment' }).click()

  await expect(page.locator('tbody tr')).toHaveCount(held - 1)

  // the sign-off itself paid it: decided, paid and signed, at once
  await page.goto('/claims/CLM-1003')
  await expect(page.locator('.badge-decided')).toBeVisible()
  await expect(page.getByText(/held for approval/i)).toHaveCount(0)
  await expect(page.getByText(/paid \$3,750\.00, signed off by dana whitfield/i)).toBeVisible()
  await expect(page.locator('.decision-approve')).toBeVisible()
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
