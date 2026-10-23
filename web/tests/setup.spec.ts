import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
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

/** Setup mode with a known key and these steps done, as `winston setup` leaves a new data directory. */
function startSetup(steps: Record<string, string> = {}) {
  mkdirSync(join(data, 'state'), { recursive: true })
  writeFileSync(state, JSON.stringify({ key_sha256: createHash('sha256').update(KEY).digest('hex'), steps }))
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
    await expect(page.getByText('Step 1 of 4: Brain and voice')).toBeVisible()
    for (const title of ['What runs his brain', 'Speaking', 'Hearing']) {
      await expect(page.locator('[data-slot="card-title"]', { hasText: title })).toBeVisible()
    }
    await fits(page, width, 'Brain and voice')

    await page.getByRole('button', { name: 'Skip for now' }).click()
    await expect(page).toHaveURL(/\/setup\/home_assistant$/)
    await expect(page.getByText('Step 2 of 4: Home Assistant')).toBeVisible()
    await fits(page, width, 'Home Assistant')
    await page.getByRole('button', { name: 'Skip for now' }).click()
    await expect(page).toHaveURL(/\/setup\/who$/)
    await expect(page.getByText('Step 3 of 4: Who he is')).toBeVisible()
    await expect(page.locator('[data-slot="card-title"]', { hasText: 'His voice' })).toBeVisible()
    await fits(page, width, 'Who he is')
    await page.getByRole('button', { name: 'Skip for now' }).click()
    await expect(page).toHaveURL(/\/setup\/done$/)
    await expect(page.getByText('Brain and voice was skipped. It waits under Set up next on Status.')).toBeVisible()
    await expect(page.getByText('Home Assistant was skipped. It waits under Set up next on Status.')).toBeVisible()
    await expect(page.getByRole('navigation', { name: 'Setup steps' }).getByRole('link', { name: /Brain and voice/ })).toContainText('(skipped)')
    await expect(page.getByText('Who can open this page', { exact: true })).toBeVisible()
    await fits(page, width, 'Done')

    await page.getByRole('button', { name: 'Finish' }).click()
    await expect(page).toHaveURL(/:\d+\/$/)

    // Status keeps the skipped step under Set up next, and its button opens the step again.
    await page.goto('/status')
    await expect(page.getByText('Brain and voice: skipped during setup.')).toBeVisible()
    await expect(page.getByText('Setup is not finished.')).toHaveCount(0)
    await page.getByRole('link', { name: 'Set it up' }).first().click()
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

test('the wizard signs in to Home Assistant, which then lists the devices he can use', async ({ page }) => {
  test.skip(!process.env.FAKE_HA_URL, 'the fake Home Assistant did not start')
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  startSetup({ brain: 'done' })
  // A new install: Home Assistant's address, and no token for it yet.
  expect((await page.request.delete('/api/secrets/HA_TOKEN')).ok()).toBe(true)
  await page.goto('/setup/home_assistant')
  await expect(page.locator('[data-slot="card-title"]', { hasText: 'Connect Home Assistant' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Continue' })).toHaveCount(0)
  // Its login (the fake signs in at once) and back, with his own token there.
  await page.getByRole('button', { name: 'Sign in to Home Assistant' }).click()
  await expect(page.getByText('Signed in as Someone. He has his own token there now, named Winston.')).toBeVisible()
  await expect(page).toHaveURL(/\/setup\/home_assistant$/)
  await expect(page.getByText(/Connected to Home Assistant/)).toBeVisible()
  await expect(page.locator('[data-slot="card-title"]', { hasText: 'Devices he can use' })).toBeVisible()
  await expect(page.locator('[data-slot="card-title"]', { hasText: 'What he can do there' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page).toHaveURL(/\/setup\/who$/)
  expect(errors).toEqual([])
})

test('the wizard renames him, and his personality follows as on the Assistant page', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  const personaFile = join(data, 'persona.md')
  const original = existsSync(personaFile) ? readFileSync(personaFile, 'utf8') : null
  startSetup({ brain: 'done', home_assistant: 'done' })
  await page.goto('/setup/who')
  const name = page.locator('#cfg_assistant_name')
  const old = await name.inputValue()
  const persona = `# ${old}\n\n## How you behave\n\n- You are ${old}, a butler and voice assistant in a home full of rooms, lamps and speakers.\n\n## Instructions\n\n- Run \`winston announce\`.\n`
  writeFileSync(personaFile, persona)
  try {
    await page.reload()
    // His personality is the document from its own page, here in the step.
    await expect(page.getByText(`You are ${old}, a butler and voice assistant`)).toBeVisible()
    // The smoke test's copy has every engine off.
    await expect(page.getByText('Speaking is off, so a voice cannot be heard.')).toBeVisible()
    await name.fill('Smokey')
    await page.getByRole('button', { name: 'Save and continue' }).click()
    const dialog = page.getByRole('alertdialog')
    await expect(dialog.getByRole('heading')).toHaveText('Rename him to Smokey?')
    await expect(dialog.getByRole('checkbox', { name: `His personality says ${old} in 2 places. Change them to Smokey?` })).toBeChecked()
    await dialog.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(page).toHaveURL(/\/setup\/done$/)
    await expect.poll(() => readFileSync(personaFile, 'utf8')).toBe(persona.replaceAll(old, 'Smokey'))
    expect(readFileSync(join(data, 'config.yaml'), 'utf8')).toContain('name: Smokey')
    await expect(page.locator('h1')).toHaveText('Set up Smokey')
  } finally {
    await page.request.patch('/api/config', { data: { changes: [{ path: ['assistant', 'name'], value: old }] } })
    if (original === null) rmSync(personaFile, { force: true })
    else writeFileSync(personaFile, original)
  }
  expect(errors).toEqual([])
})
