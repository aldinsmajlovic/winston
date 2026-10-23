import { createHash } from 'node:crypto'
import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'

// The setup wizard (pages/setup.tsx) on the throwaway winstond `pnpm smoke` starts, put in setup mode by
// writing its state file as `winston setup` does. This computer needs no key; another device's need for one
// is tests/setup_link_cases.py's.
const data = process.env.WINSTON_DATA ?? ''
const state = join(data, 'state', 'setup.json')
const KEY = '2345-6789-abcd'

test.describe.configure({ mode: 'serial' })

test.beforeAll(() => {
  if (!process.env.WINSTON_URL || !data) throw new Error('Run with `pnpm smoke`: it starts the throwaway winstond')
})

test.afterAll(() => rmSync(state, { force: true }))

/** Setup mode with a known key and no step done, as `winston setup` leaves a new data directory. */
function startSetup() {
  mkdirSync(join(data, 'state'), { recursive: true })
  writeFileSync(state, JSON.stringify({ key_sha256: createHash('sha256').update(KEY).digest('hex') }))
}

async function fits(page: Page, width: number, label: string) {
  await expect(page.locator('[data-slot="skeleton"]')).toHaveCount(0)
  expect(await page.locator('html').evaluate((html) => html.scrollWidth), label).toBeLessThanOrEqual(width)
}

for (const [label, width, height] of [['desktop', 1400, 860], ['phone', 390, 844]] as const) {
  test(`the wizard at ${label} width: a step skipped, Finish, and the step in Set up next`, async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(e.message))
    startSetup()
    await page.setViewportSize({ width, height })

    // The setup link: its key leaves the address bar, and the first step not done opens.
    await page.goto(`/setup?key=${KEY}`)
    await expect(page).toHaveURL(/\/setup\/brain$/)
    await expect(page.locator('h1')).toHaveText(/^Set up \S/)
    await expect(page.getByText('Step 1 of 2: Brain and voice')).toBeVisible()
    for (const title of ['What runs his brain', 'Speaking', 'Hearing']) {
      await expect(page.locator('[data-slot="card-title"]', { hasText: title })).toBeVisible()
    }
    await fits(page, width, 'Brain and voice')

    await page.getByRole('button', { name: 'Skip for now' }).click()
    await expect(page).toHaveURL(/\/setup\/done$/)
    await expect(page.getByText('Brain and voice was skipped. It waits under Set up next on Status.')).toBeVisible()
    await expect(page.getByRole('navigation', { name: 'Setup steps' }).getByRole('link', { name: /Brain and voice/ })).toContainText('(skipped)')
    await expect(page.getByText('Who can open this page', { exact: true })).toBeVisible()
    await fits(page, width, 'Done')

    await page.getByRole('button', { name: 'Finish' }).click()
    await expect(page).toHaveURL(/:\d+\/$/)

    // Status keeps the skipped step under Set up next, and its button opens the step again.
    await page.goto('/status')
    await expect(page.getByText('Brain and voice: skipped during setup.')).toBeVisible()
    await expect(page.getByText('Setup is not finished.')).toHaveCount(0)
    await page.getByRole('link', { name: 'Set it up' }).click()
    await expect(page).toHaveURL(/\/setup\/brain$/)
    expect(errors).toEqual([])
  })
}

test('while setup is not finished, Status says so and leads back to it', async ({ page }) => {
  startSetup()
  await page.goto('/status')
  await page.getByRole('link', { name: 'Continue setup' }).click()
  await expect(page).toHaveURL(/\/setup\/brain$/)
})
