import { expect, test as base, type APIRequestContext, type Page } from '@playwright/test'

export const PASSWORD = 'e2e-password'
export const HANDLERS = {
  dana: { email: 'dana.whitfield@marvinsurance.example', name: 'Dana Whitfield' },
  marcus: { email: 'marcus.oyelaran@marvinsurance.example', name: 'Marcus Oyelaran' },
}
const AUTH = 'http://127.0.0.1:8004'

/** Sign in through the dashboard's own form, against the stand-in for Supabase Auth. */
export async function signIn(page: Page, who: keyof typeof HANDLERS = 'dana') {
  await page.goto('/')
  await page.getByLabel('Email').fill(HANDLERS[who].email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
}

/** An access token for a handler, for specs that play something out through the API. */
export async function tokenFor(request: APIRequestContext, who: keyof typeof HANDLERS = 'dana') {
  const answer = await request.post(`${AUTH}/auth/v1/token?grant_type=password`, { data: { email: HANDLERS[who].email, password: PASSWORD } })
  return (await answer.json()).access_token as string
}

export const asHandler = async (request: APIRequestContext, who: keyof typeof HANDLERS = 'dana') => ({
  Authorization: `Bearer ${await tokenFor(request, who)}`,
})

/** Every spec starts signed in as Dana Whitfield, the senior handler. */
export const test = base.extend({
  page: async ({ page }, use) => {
    await signIn(page)
    await use(page)
  },
})

export { expect }
