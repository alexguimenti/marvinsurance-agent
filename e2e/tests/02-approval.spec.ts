import type { APIRequestContext } from '@playwright/test'
import { asHandler, expect, test } from './fixtures'

test.beforeAll(async ({ request }) => {
  // make sure the queue has run, in case this file is run on its own
  await request.post('/api/dispatches', { data: { workers: 5 }, headers: await asHandler(request) })
  await expect
    .poll(async () => (await (await request.get('/api/approvals')).json()).length, { timeout: 30_000 })
    .toBe(3)
})

const received = async (request: APIRequestContext) =>
  (await (await request.get('http://127.0.0.1:8003/received')).json()).resumes

test('a sign-off on the dashboard reaches the agent service, and the claim shows in progress', async ({ page, request }) => {
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
  expect(await received(request)).toEqual([])

  await expect(page.getByText('Signing off as Dana Whitfield')).toBeVisible()
  await row.getByRole('button', { name: 'Approve payment' }).click()

  await expect(page.locator('tbody tr')).toHaveCount(2)
  await expect.poll(() => received(request)).toEqual(['CLM-1003'])

  // the stand-in does not pay, so the claim stays between the sign-off and the payment
  await page.goto('/claims/CLM-1003')
  await expect(page.locator('.badge-in_progress')).toBeVisible()
  await expect(page.getByText(/held for approval/i)).toHaveCount(0)
  await expect(page.getByText(/signed off by dana whitfield/i)).toBeVisible()
  await expect(page.locator('.decision-approve')).toHaveCount(0)
})

test('approving the rest empties the approvals list, and each sign-off reaches the service', async ({ page, request }) => {
  await page.goto('/approvals')
  // wait for the list to load: counting rows right after goto() would see zero and skip the loop
  await expect(page.locator('tbody tr')).not.toHaveCount(0)
  while ((await page.locator('tbody tr').count()) > 0) {
    const before = await page.locator('tbody tr').count()
    await page.getByRole('button', { name: 'Approve payment' }).first().click()
    await expect(page.locator('tbody tr')).toHaveCount(before - 1)
  }
  await expect(page.getByText(/nothing is waiting for approval/i)).toBeVisible()
  await expect.poll(() => received(request)).toEqual(['CLM-1003', 'CLM-1008', 'CLM-1011'])
})
