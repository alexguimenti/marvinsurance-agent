import type { Page } from '@playwright/test'
import { asHandler, expect, test } from './fixtures'

const received = async (page: Page) => (await (await page.request.get('http://127.0.0.1:8003/received')).json()).starts

/**
 * The queue page's counts, which an evaluation must leave as they were. A claim signed off earlier is paid by the
 * service's own 30 second sweep, which moves it from In progress to Decided whatever the evaluation does, so those two
 * count together.
 */
async function queueCounts(page: Page) {
  await page.goto('/')
  await expect(page.locator('tbody tr')).toHaveCount(26)
  const tiles = await page.locator('.tile').allTextContents()
  const n = (label: string) => Number(tiles.find((t) => t.endsWith(label))?.replace(label, '') ?? NaN)
  return {
    waiting: n('Waiting'),
    awaiting: n('Awaiting approval'),
    handler: n('Needs a handler'),
    inProgressOrDecided: n('In progress') + n('Decided'),
    claims: await page.locator('tbody tr').count(),
  }
}

test('an evaluation started on the dashboard reaches the service with N, and shows on the scoreboard', async ({ page }) => {
  const before = await queueCounts(page)

  await page.goto('/scoreboard')
  await expect(page.getByText(/No evaluations yet/)).toBeVisible()
  await page.getByLabel('Set').selectOption('held_out')
  await page.getByLabel('Only the five tiny claims').check()
  await page.getByLabel('Claim agents at once').fill('5')
  await page.getByRole('button', { name: 'Start evaluation' }).click()

  await expect.poll(() => received(page)).toContainEqual({ path: '/evaluations', body: { eval_set: 'held_out', tiny: true, workers: 5 } })
  const row = page.locator('tbody tr').first()
  await expect(row).toContainText('Finished', { timeout: 60_000 })
  await expect(row).toContainText('Held-out set, tiny')
  for (const name of ['Decision accuracy', 'Accuracy', 'Grade', 'Cost of mistakes', 'Straight-through', 'Cost per claim', 'Time per claim', 'Total time']) {
    await expect(page.getByRole('columnheader', { name, exact: true })).toBeVisible()
  }

  await row.getByRole('link').first().click()
  await expect(page.getByRole('heading', { name: /Evaluation \d+/ })).toBeVisible()
  await expect(page.locator('tbody tr')).toHaveCount(5)
  await expect(page.locator('.pill-grade-B').first()).toBeVisible() // the judge's stand-in grades every explanation B
  await page.locator('tbody tr').first().getByRole('link').click()
  await expect(page.getByText(/Evaluation copy of/)).toBeVisible()

  expect(await queueCounts(page)).toEqual(before)
})

test('an evaluation played out through the API appears on the scoreboard', async ({ page }) => {
  const answer = await page.request.post('/api/evaluations', { data: { eval_set: 'evaluation', tiny: true, workers: 3 }, headers: await asHandler(page.request) })
  expect(answer.status()).toBe(202)
  await page.goto('/scoreboard')
  await expect(page.locator('tbody tr')).toHaveCount(2)
  const newest = page.locator('tbody tr').first()
  await expect(newest).toContainText('Evaluation set, tiny')
  await expect(newest).toContainText('Finished', { timeout: 60_000 })
  await expect(newest).toContainText('%')
})
