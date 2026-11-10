import { asHandler, expect, test } from './fixtures'

// A whole round on the scripted improver and claim agent: the page shows it as it happens, a handler approves the
// change in the dashboard's own dialog, and the recipe's approval is played out through the API.
test('a round shows as it happens, and a handler approves its proposals', async ({ page, request }) => {
  test.setTimeout(240_000)
  await page.goto('/improvement')
  await expect(page.getByRole('heading', { name: 'Improvement' })).toBeVisible()
  await expect(page.getByText(/No rounds yet/)).toBeVisible()

  await page.getByRole('button', { name: 'Start round' }).click()
  const start = page.getByRole('dialog')
  await expect(start).toContainText('spends up to $0.50') // the starting spending limit: the Settings spec changes it afterwards
  await start.getByRole('button', { name: 'Start the round' }).click()

  const open = page.getByRole('region', { name: 'Open round' })
  await expect(open).toContainText('Round 1')
  const waiting = page.getByRole('region', { name: 'Waiting for your decision' })
  await expect(waiting).toContainText('A change to the claim agent is proposed', { timeout: 120_000 })
  await expect(waiting.locator('.diff-add').first()).toContainText('receipt')
  await expect(open.getByRole('listitem').filter({ hasText: 'A change waiting for approval' })).toHaveAttribute('aria-current', 'step')

  await waiting.getByRole('button', { name: 'Approve' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Approve the change' }).click()

  await expect(waiting).toContainText('A new recipe is proposed', { timeout: 180_000 })
  const row = page.getByRole('table', { name: 'Rounds' }).locator('tbody tr').first()
  await expect(row).toContainText('Rolled back') // the scripted claim agent is nowhere near the objectives
  await expect(row).toContainText('Dana Whitfield approved the change')

  const view = await (await request.get('/api/rounds/1', { headers: await asHandler(request) })).json()
  const answer = await request.post('/api/rounds/1/decision', {
    data: { proposal_id: view.waiting.id, approve: true, reason: '' },
    headers: await asHandler(request),
  })
  expect(answer.status()).toBe(202)

  await expect(row).toContainText('Dana Whitfield approved the recipe', { timeout: 60_000 })
  await expect(open).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Start round' })).toBeEnabled()
  await expect(page.getByRole('img', { name: 'Held-out accuracy of each version tried' })).toBeVisible()

  await page.goto('/configuration')
  const versions = page.getByRole('table', { name: 'Versions', exact: true })
  await expect(versions.locator('tbody tr')).toHaveCount(2)
  await expect(page.getByRole('table', { name: 'Recipe versions' }).locator('tbody tr')).toHaveCount(2)
})
