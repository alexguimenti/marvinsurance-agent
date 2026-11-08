import { expect, signIn, test } from './fixtures'

test('a senior handler changes a setting after confirming, and the page says who', async ({ page }) => {
  await page.goto('/settings')
  const limit = page.getByLabel('Spending limit for each round (USD)')
  await expect(limit).toHaveValue('0.5')
  await limit.fill('0.75')
  await page.getByRole('button', { name: 'Save spending limit for each round' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('$0.50 to $0.75')
  await dialog.getByRole('button', { name: 'Change the setting' }).click()
  await expect(dialog).toHaveCount(0)
  const section = page.locator('form', { has: limit })
  await expect(section).toContainText('Last changed by Dana Whitfield')
  await page.reload()
  await expect(page.getByLabel('Spending limit for each round (USD)')).toHaveValue('0.75')
})

test('the appearance switches to dark and is remembered after a reload', async ({ page }) => {
  await page.goto('/settings')
  await page.getByRole('radio', { name: 'Dark' }).check()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.getByRole('radio', { name: 'Light' }).check()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
})

test("a handler who can't sign off over the limit sees the settings but cannot change them", async ({ browser }) => {
  const context = await browser.newContext({ baseURL: 'http://127.0.0.1:5273' })
  const page = await context.newPage()
  await signIn(page, 'marcus')
  await page.goto('/settings')
  await expect(page.getByLabel('Approval limit (USD)')).toBeDisabled()
  await expect(page.getByText(/Only a handler who can sign off payouts over the limit can change settings/)).toBeVisible()
  await expect(page.getByRole('button', { name: /^Save/ })).toHaveCount(0)
  await context.close()
})
