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
    await expect(page.getByText('Step 1 of 5: Brain and voice')).toBeVisible()
    // The picks in one card; Change opens a pick's card from the Brain and Voice pages, and Done closes it.
    await expect(page.locator('[data-slot="card-title"]', { hasText: 'Picked for this computer' })).toBeVisible()
    await expect(page.locator('[data-slot="card-title"]', { hasText: 'What runs his brain' })).toHaveCount(0)
    await expect(page.getByRole('link', { name: 'Back' })).toHaveCount(0)  // the first step
    for (const title of ['What runs his brain', 'Speaking', 'Hearing']) {
      await page.getByRole('button', { name: `Change ${title.toLowerCase()}` }).click()
      await expect(page.locator('[data-slot="card-title"]', { hasText: title })).toBeVisible()
    }
    // Every pick there is a preset: How it runs stays on the Brain and Voice pages.
    await expect(page.getByRole('button', { name: 'How it runs', exact: true })).toHaveCount(0)
    await fits(page, width, 'Brain and voice')
    await page.getByRole('button', { name: 'Done with speaking' }).click()
    await expect(page.locator('[data-slot="card-title"]', { hasText: 'Speaking' })).toHaveCount(0)

    await page.getByRole('button', { name: 'Skip for now' }).click()
    await expect(page).toHaveURL(/\/setup\/home_assistant$/)
    await expect(page.getByText('Step 2 of 5: Home Assistant')).toBeVisible()
    // Back goes to the step before, as it was left.
    await page.getByRole('link', { name: 'Back' }).click()
    await expect(page).toHaveURL(/\/setup\/brain$/)
    await page.goBack()
    await expect(page).toHaveURL(/\/setup\/home_assistant$/)
    await fits(page, width, 'Home Assistant')
    await page.getByRole('button', { name: 'Skip for now' }).click()
    await expect(page).toHaveURL(/\/setup\/who$/)
    await expect(page.getByText('Step 3 of 5: Who he is')).toBeVisible()
    await expect(page.locator('[data-slot="card-title"]', { hasText: 'His voice' })).toBeVisible()
    await fits(page, width, 'Who he is')
    await page.getByRole('button', { name: 'Skip for now' }).click()
    await expect(page).toHaveURL(/\/setup\/box$/)
    await expect(page.getByText('Step 4 of 5: A room box')).toBeVisible()
    // The smoke test's computer has no Voice PE plugged in.
    await expect(page.getByText('Waiting for a Voice PE on USB-C.')).toBeVisible()
    await fits(page, width, 'A room box')
    await page.getByRole('button', { name: 'Skip for now' }).click()
    await expect(page).toHaveURL(/\/setup\/done$/)
    await expect(page.getByText('A room box was skipped. It waits under Set up next on Status.')).toBeVisible()
    await expect(page.getByText('Brain and voice was skipped. It waits under Set up next on Status.')).toBeVisible()
    await expect(page.getByText('Home Assistant was skipped. It waits under Set up next on Status.')).toBeVisible()
    await expect(page.getByRole('navigation', { name: 'Setup steps' }).getByRole('link', { name: /Brain and voice/ })).toContainText('(skipped)')
    await expect(page.getByText('Step 5 of 5: Who can open his pages')).toBeVisible()
    await expect(page.getByText('Who can open this page', { exact: true })).toBeVisible()
    await fits(page, width, 'Who can open his pages')
    // Open to the network, it gives the address a phone opens, when this computer has one; then back.
    const { address } = await (await page.request.get('/api/setup')).json()
    const who = page.getByRole('combobox', { name: 'Who can open this page' })
    await who.click()
    await page.getByRole('option', { name: 'Every device on your network' }).click()
    if (address) await expect(page.getByText(`From a phone or another device here, open http://${address}:`)).toBeVisible()
    await who.click()
    await page.getByRole('option', { name: 'This computer only' }).click()

    await page.getByRole('button', { name: 'Finish and go to Talk' }).click()
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

test('the steps save without restarting; Finish restarts what waits, except one still installing', async ({ page }) => {
  startSetup({ brain: 'done', home_assistant: 'done', who: 'done', box: 'done' })
  // The smoke test runs one stand-in engine: two made from it, running with saved changes waiting.
  await page.route('**/api/status', async (r) => {
    const status = await (await r.fetch()).json()
    const [e] = status.engines
    status.engines = [
      { ...e, name: 'tts', state: 'healthy', config_changed: true },
      { ...e, name: 'stt', state: 'installing', config_changed: true },
    ]
    await r.fulfill({ json: status })
  })
  const restarted: string[] = []
  await page.route('**/api/engines/*/restart', (r) => {
    restarted.push(r.request().url().split('/').at(-2)!)
    return r.fulfill({ json: { ok: true } })
  })
  await page.goto('/setup/done')
  await page.getByRole('button', { name: 'Finish and go to Talk' }).click()
  await expect(page).toHaveURL(/:\d+\/$/)
  await expect(page.getByRole('alertdialog')).toHaveCount(0)
  await expect.poll(() => restarted).toEqual(['tts'])
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('while setup is not finished, his address opens the wizard', async ({ page }) => {
  startSetup()
  await page.goto('/')
  await expect(page).toHaveURL(/\/setup\/brain$/)
})

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
  // Its login opens in a new tab (the fake signs in at once), which hands the result back here and closes.
  const login = page.waitForEvent('popup')
  await page.getByRole('button', { name: 'Sign in to Home Assistant' }).click()
  await (await login).waitForEvent('close')
  await expect(page.getByText('Signed in as Someone. He has his own token there now, named Winston.')).toBeVisible()
  await expect(page).toHaveURL(/\/setup\/home_assistant$/)
  await expect(page.getByText(/Connected to Home Assistant/)).toBeVisible()
  await expect(page.locator('[data-slot="card-title"]', { hasText: 'Devices he can use' })).toBeVisible()
  await expect(page.locator('[data-slot="card-title"]', { hasText: 'What he can do there' })).toHaveCount(0)
  // Both ways Home Assistant talks to him, in one card, off until switched on.
  await expect(page.locator('[data-slot="card-title"]', { hasText: 'From Home Assistant' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'The integration', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Assist', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page).toHaveURL(/\/setup\/who$/)
  expect(errors).toEqual([])
})

test('the wizard renames him without asking, and his personality follows as on the Assistant page', async ({ page }) => {
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
    // His personality's first rule is in the step; the document from its own page folds under it.
    await expect(page.getByText(`You are ${old}, a butler and voice assistant`)).toBeVisible()
    // The smoke test's copy has every engine off.
    await expect(page.getByText('Speaking is off, so a voice cannot be heard.')).toBeVisible()
    await name.fill('Smokey')
    // The wizard asks nothing: it takes the confirm's default and renames him in his personality too.
    await page.getByRole('button', { name: 'Save and continue' }).click()
    await expect(page.getByRole('alertdialog')).toHaveCount(0)
    await expect(page).toHaveURL(/\/setup\/box$/)
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

test('a room box added when Home Assistant does not disable its old entry: said, and the wizard goes on', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  startSetup({ brain: 'done', home_assistant: 'done', who: 'done' })
  const before = (await (await page.request.get('/api/config')).json()).channels.satellite
  // The box as the step's routes describe it, flashed and joined at a documentation address (RFC 5737),
  // with Home Assistant's old entry for it, which Home Assistant then refuses to disable.
  const port = '/dev/ttyACM0'
  const refused = 'Home Assistant did not disable its old entry for the box (offline). Disable it there by hand, under ESPHome.'
  await page.route('**/api/setup/box', (r) => r.fulfill({ json: { ports: [port], info: { firmware: 'Home Assistant Voice PE', version: '25.10.0', winston: false }, flash: { port, stage: 'done', percent: 100, error: '' } } }))
  await page.route('**/api/setup/box/networks', (r) => r.fulfill({ json: [{ ssid: 'Home', rssi: -50, secured: true }] }))
  await page.route('**/api/setup/box/wifi', (r) => r.fulfill({ json: { host: '192.0.2.50', mac: '00:00:00:00:00:01', ha_entry: { entry_id: 'entry_box', title: 'Home Assistant Voice 0a1b2c', disabled: false } } }))
  await page.route('**/api/setup/box/ha-disable', (r) => r.fulfill({ status: 502, json: { detail: refused } }))
  try {
    await page.goto('/setup/box')
    await page.getByRole('combobox', { name: 'Network' }).click()
    await page.getByRole('option', { name: 'Home Needs a password' }).click()
    await page.getByLabel('Password', { exact: true }).fill('a password')
    await page.getByRole('button', { name: 'Connect' }).click()
    await expect(page.getByText('It joined Home, and Winston reaches it at 192.0.2.50.')).toBeVisible()
    await expect(page.getByRole('switch', { name: 'Disable it in Home Assistant' })).toBeChecked()
    await page.getByLabel('Room', { exact: true }).fill('Smoke room')
    await page.getByRole('button', { name: 'Add the box and continue' }).click()
    await expect(page.getByText(refused)).toBeVisible()
    await expect(page.getByText('A box is in that room already.')).toHaveCount(0)
    await page.getByRole('button', { name: 'Continue' }).click()
    await expect(page).toHaveURL(/\/setup\/done$/)
    await expect(page.getByText('A room box was skipped.', { exact: false })).toHaveCount(0)
    const saved = (await (await page.request.get('/api/config')).json()).channels.satellite
    expect(saved.enabled).toBe(true)
    expect(saved.devices['Smoke room']).toMatchObject({ host: '192.0.2.50' })
    // Ways to talk lists it like a box added there.
    await page.goto('/ways-to-talk')
    await expect(page.getByRole('row', { name: /^Smoke room / })).toBeVisible()
  } finally {
    await page.request.patch('/api/config', { data: { changes: [{ path: ['channels', 'satellite'], value: before }] } })
  }
  expect(errors).toEqual([])
})
