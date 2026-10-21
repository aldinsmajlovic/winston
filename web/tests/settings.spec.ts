import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'

// Runs against the throwaway winstond `pnpm smoke` starts: WINSTON_DATA is its copy of data/, with the
// engines and channels switched off and one external engine, `smoke`, that nothing runs.
const data = process.env.WINSTON_DATA ?? ''
const pages = ['/status', '/assistant', '/assistant/personality', '/brain', '/voice', '/ways-to-talk', '/home-assistant', '/extensions', '/actions', '/speaking-up', '/extensions/plants']

test.describe.configure({ mode: 'serial' })

test.beforeAll(() => {
  if (!process.env.WINSTON_URL || !data) throw new Error('Run with `pnpm smoke`: it starts the throwaway winstond')
})

/** Fails the test on any uncaught error in the page. */
function watchErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  return errors
}

async function open(page: Page, path: string) {
  await page.goto(path)
  await expect(page.locator('h1')).toBeVisible()
  await expect(page.locator('[data-slot="skeleton"]')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Retry' })).toHaveCount(0)
}

/** Open every closed How it runs on the page, so what it holds is drawn too. */
async function openFolds(page: Page) {
  for (const fold of await page.getByRole('button', { name: 'How it runs', exact: true }).all()) {
    if ((await fold.getAttribute('aria-expanded')) !== 'true') await fold.click()
    await expect(fold).toHaveAttribute('aria-expanded', 'true')
  }
}

/** Save from the header; `choice` answers the confirmation a restart or a key asks for. */
async function save(page: Page, choice?: string) {
  const button = page.locator('header').getByRole('button', { name: 'Save', exact: true })
  await button.click()
  const dialog = page.getByRole('alertdialog')
  if (choice) await dialog.getByRole('button', { name: choice, exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(button).toBeDisabled()
}

test('every settings page loads, with nothing to save until something is changed', async ({ page }) => {
  const errors = watchErrors(page)
  for (const path of pages) {
    await open(page, path)
    // No page-wide view mode: a field is drawn or it is config.yaml only.
    await expect(page.locator('header').getByRole('button', { name: 'Advanced', exact: true })).toHaveCount(0)
    // A form filling in its defaults is not a change: the plants card (off, so its config has no entry)
    // shows Below at its default of 30, and redrawing the forms (to the store and back, in the app) must
    // not save it.
    if (path === '/extensions/plants') {
      await expect(page.getByLabel('Below', { exact: true })).toHaveValue('30', { timeout: 30_000 })
      await page.locator('header').getByRole('link', { name: 'Extensions', exact: true }).click()
      await expect(page).toHaveURL(/\/extensions$/)
      await page.locator('a[href="/extensions/plants"]').click()
      await expect(page.getByLabel('Below', { exact: true })).toHaveValue('30')
    }
    const saveButton = page.locator('header').getByRole('button', { name: 'Save', exact: true })
    if (await saveButton.count()) {
      await page.waitForTimeout(500)
      await expect(saveButton, `${path} opened with unsaved changes`).toBeDisabled()
    }
  }
  expect(errors).toEqual([])
})

test('every page fits a phone: no sideways scroll at 390 px, with every How it runs open', async ({ page }) => {
  const errors = watchErrors(page)
  await page.setViewportSize({ width: 390, height: 844 })
  const fits = async (label: string) => {
    expect(await page.locator('html').evaluate((html) => html.scrollWidth), label).toBeLessThanOrEqual(390)
    // A card clips what is wider than it, so the page cannot see it: no card's content is wider than the card.
    const clipped = await page.locator('[data-slot="card"]').evaluateAll((cards) =>
      cards.filter((c) => c.scrollWidth > c.clientWidth + 1).map((c) => c.querySelector('[data-slot="card-title"]')?.textContent),
    )
    expect(clipped, `${label}: cards cut at the right`).toEqual([])
  }
  for (const path of ['/', '/history', '/memory', '/wall', ...pages, '/extensions/news']) {
    if (path === '/wall') {
      await page.goto(path)
      await expect(page.getByRole('link', { name: 'Close', exact: true })).toBeVisible()     // no header: the panel's way back
    } else await open(page, path)
    // Open, a fold only adds below what was there: the engine forms (arguments, environment, volumes) fit too.
    await openFolds(page)
    await fits(path)
    // The page's own name stays whole; the breadcrumb and the commands give way first.
    if (path !== '/wall') expect(await page.locator('h1').evaluate((h) => h.scrollWidth <= h.clientWidth), `${path} title cut`).toBe(true)
  }
  // The header's More menu, where two or more commands fold, is as wide as its commands: none wraps onto a second line.
  const more = page.locator('header').getByRole('button', { name: 'More', exact: true })
  if (await more.count()) {
    await more.click()
    for (const item of await page.getByRole('menuitem').all()) expect((await item.boundingBox())?.height ?? 0).toBeLessThan(40)
    await page.keyboard.press('Escape')
  }
  // A header's only command stays a button: a menu of one saves no room.
  await open(page, '/extensions')
  await expect(page.locator('header').getByRole('button', { name: 'Add an MCP server', exact: true })).toBeVisible()
  expect(errors).toEqual([])
})

test('an extension page fits beside the sidebar: no sideways scroll at 1280 px', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  for (const path of ['/extensions/news', '/extensions/plants', '/home-assistant', '/status']) {
    await open(page, path)
    expect(await page.locator('html').evaluate((html) => html.scrollWidth), path).toBeLessThanOrEqual(1280)
  }
})

test('a rename is saved, still there after a reload, and changes his persona only when kept checked', async ({ page }) => {
  const personaFile = join(data, 'persona.md')
  const original = existsSync(personaFile) ? readFileSync(personaFile, 'utf8') : null
  await open(page, '/assistant')
  const name = page.locator('#cfg_assistant_name')
  const old = await name.inputValue()
  const persona = `# ${old}\n\n## How you behave\n\n- You are ${old}, a butler and voice assistant in a home full of rooms, lamps and speakers.\n\n## Instructions\n\n- Run \`winston announce\`.\n`
  writeFileSync(personaFile, persona)
  try {
    await name.fill('Smokey')
    await page.locator('header').getByRole('button', { name: 'Save', exact: true }).click()
    const dialog = page.getByRole('alertdialog')
    await expect(dialog.getByRole('heading')).toHaveText('Rename him to Smokey?')
    const check = dialog.getByRole('checkbox', { name: `His personality says ${old} in 2 places. Change them to Smokey?` })
    await expect(check).toBeChecked()
    const line = dialog.getByText(`You are ${old}, a butler and voice assistant`)
    await expect(line).toBeVisible()
    // A long line is cut short inside the dialog, never pushing the checkbox or the buttons past its edge.
    const edge = await dialog.boundingBox()
    for (const part of [line, check, dialog.getByRole('button', { name: 'Save', exact: true })]) {
      const box = await part.boundingBox()
      expect(box!.x + box!.width).toBeLessThanOrEqual(edge!.x + edge!.width)
    }
    await dialog.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(dialog).toHaveCount(0)
    await expect.poll(() => readFileSync(personaFile, 'utf8')).toBe(persona.replaceAll(old, 'Smokey'))
    expect(readFileSync(`${personaFile}.bak`, 'utf8')).toBe(persona)
    await page.reload()
    await expect(page.locator('#cfg_assistant_name')).toHaveValue('Smokey')
    expect(readFileSync(join(data, 'config.yaml'), 'utf8')).toContain('name: Smokey')

    // Back to the old name with the box unchecked: the config changes, the persona does not.
    const renamed = readFileSync(personaFile, 'utf8')
    await page.locator('#cfg_assistant_name').fill(old)
    await page.locator('header').getByRole('button', { name: 'Save', exact: true }).click()
    await dialog.getByRole('checkbox').click()
    await expect(dialog.getByRole('checkbox')).not.toBeChecked()
    await dialog.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(dialog).toHaveCount(0)
    await expect(page.locator('header').getByRole('button', { name: 'Save', exact: true })).toBeDisabled()
    expect(readFileSync(join(data, 'config.yaml'), 'utf8')).toContain(`name: ${old}`)
    expect(readFileSync(personaFile, 'utf8')).toBe(renamed)
  } finally {
    if (original === null) rmSync(personaFile, { force: true })
    else writeFileSync(personaFile, original)
  }
})

test('a pasted key shows as Saved and never comes back from the server', async ({ page }) => {
  const secret = `smoke-${Date.now()}-${Math.random().toString(36).slice(2)}`
  const leaks: string[] = []
  let seen = 0
  page.on('response', async (r) => {
    if (!r.url().includes('/api/') || (r.headers()['content-type'] ?? '').includes('event-stream')) return
    const text = await r.text().catch(() => '')
    seen += 1
    if (text.includes(secret) || JSON.stringify(r.headers()).includes(secret)) leaks.push(r.url())
  })

  await open(page, '/ways-to-talk')
  // The key is pasted; the variable it is stored under is config.yaml only.
  await expect(page.getByText('Token variable', { exact: true })).toHaveCount(0)
  // Exact: once a token is saved, the contacts field checks it with Discord and a made-up one gets an
  // icon labelled "Discord refused the bot token", which a loose match would find too. The key's state is
  // a word beside the field, and Discord is off in the copy: a key under a switched-off section never warns.
  const field = page.getByLabel('Bot token', { exact: true })
  await expect(field.locator('..').getByText('Not set', { exact: true })).toHaveCount(0)
  await expect(field).toHaveAttribute('placeholder', 'Paste the bot token')
  await field.fill(secret)
  await save(page)
  await page.reload()
  await open(page, '/ways-to-talk')
  const row = page.getByLabel('Bot token', { exact: true }).locator('..')
  await expect(row.getByText('Saved', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Bot token', { exact: true })).toHaveAttribute('placeholder', 'Paste to replace it')
  await expect(page.getByLabel('Bot token', { exact: true })).toHaveValue('')

  for (const url of ['/api/config', '/api/secrets', '/api/status', '/api/config/schema']) {
    const body = await (await page.request.get(url)).text()
    expect(body, url).not.toContain(secret)
  }
  expect(seen).toBeGreaterThan(0)
  expect(leaks).toEqual([])
  expect(readFileSync(join(data, '.env'), 'utf8')).toContain(secret)
  expect(readFileSync(join(data, 'config.yaml'), 'utf8')).not.toContain(secret)
})

test('an MCP server added by URL keeps its token in data/.env alone, and its page shows it as Saved', async ({ page }) => {
  const secret = `smoke-mcp-${Date.now()}-${Math.random().toString(36).slice(2)}`
  const errors = watchErrors(page)
  await open(page, '/extensions')
  await page.getByRole('button', { name: 'Add an MCP server' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Name', { exact: true }).fill('smoke_http')
  await expect(dialog.getByLabel('Token', { exact: true })).toHaveCount(0)
  await dialog.getByLabel('How it starts').click()
  await page.getByRole('option', { name: 'An address' }).click()
  await dialog.getByLabel('Address', { exact: true }).fill('http://127.0.0.1:9/mcp')
  await dialog.getByLabel('Token', { exact: true }).fill(secret)
  await dialog.getByRole('button', { name: 'Add', exact: true }).click()
  // The server's page opens from the draft; Save stores it.
  await expect(page).toHaveURL(/\/extensions\/smoke_http$/)
  await page.locator('header').getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByRole('alertdialog').getByText('Extensions: smoke_http, Token')).toBeVisible()
  await page.getByRole('alertdialog').getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByRole('alertdialog')).toHaveCount(0)
  await open(page, '/extensions')
  await page.getByRole('link', { name: 'smoke_http' }).click()
  await expect(page).toHaveURL(/\/extensions\/smoke_http$/)
  const field = page.getByLabel('Token', { exact: true })
  await expect(field.locator('..').getByText('Saved', { exact: true })).toBeVisible()
  await expect(field).toHaveValue('')
  for (const url of ['/api/config', '/api/secrets', '/api/extensions', '/api/config/schema']) {
    const body = await (await page.request.get(url)).text()
    expect(body, url).not.toContain(secret)
  }
  expect(readFileSync(join(data, '.env'), 'utf8')).toMatch(new RegExp(`^SMOKE_HTTP_MCP_TOKEN=['"]?${secret}['"]?$`, 'm'))
  const config = readFileSync(join(data, 'config.yaml'), 'utf8')
  expect(config).toContain('token_env: SMOKE_HTTP_MCP_TOKEN')
  expect(config).not.toContain(secret)
  expect(errors).toEqual([])
})

test('a field for hosted engines is left out for a local one', async ({ page }) => {
  // The copy's brain runs in Docker: its API address sits in How it runs, beside the port, and it has no key.
  await open(page, '/brain')
  const fold = page.getByRole('button', { name: 'How it runs', exact: true })
  await expect(fold).toHaveAttribute('aria-expanded', 'false')
  await expect(page.locator('#cfg_llm_base_url')).toHaveCount(0)
  await fold.click()
  await expect(page.locator('#cfg_llm_base_url')).toBeVisible()
  await expect(page.locator('#cfg_llm_api_key_env__secret')).toHaveCount(0)
  await expect(page.getByText('Key variable', { exact: true })).toHaveCount(0)
})

test('a refused save opens the How it runs that holds the field', async ({ page }) => {
  const msg = 'Input should be greater than or equal to 10'
  await page.route('**/api/config?*dry_run=true*', (route) =>
    route.fulfill({ status: 422, json: { detail: [{ loc: ['body', 'engines', 'llm', 'startup_timeout'], msg, type: 'greater_than_equal' }] } }),
  )
  const errors = watchErrors(page)
  await open(page, '/brain')
  const fold = page.getByRole('button', { name: 'How it runs', exact: true })
  await expect(fold).toHaveAttribute('aria-expanded', 'false')
  await page.locator('#cfg_llm_sampling_max_tokens').fill('777')
  await page.locator('header').getByRole('button', { name: 'Save', exact: true }).click()
  await expect(fold).toHaveAttribute('aria-expanded', 'true')
  await expect(page.locator('#cfg_engines_llm_startup_timeout')).toBeVisible()
  await expect(page.locator('#cfg_engines_llm').getByText(msg, { exact: true })).toBeVisible()
  // While the error stands, the fold stays open.
  await fold.click()
  await expect(fold).toHaveAttribute('aria-expanded', 'true')
  const toast = page.locator('[data-sonner-toast]').filter({ hasText: 'Not saved' })
  await expect(toast).toContainText(`Brain, `)
  await expect(toast).toContainText(msg)
  await page.getByRole('button', { name: 'Discard', exact: true }).click()
  expect(errors).toEqual([])
})

test('Status lists the custom engines and who can open this page', async ({ page }) => {
  await open(page, '/status')
  // A custom engine has no row in His parts, so its card shows while there is one.
  const custom = page.locator('[data-slot="card"]').filter({ has: page.getByText('Custom engines', { exact: true }) })
  await expect(custom.getByText('smoke', { exact: true })).toBeVisible()
  const header = page.locator('header')
  const add = header.getByRole('button', { name: 'Add a custom engine', exact: true })
  if (!(await add.isVisible())) {
    await header.getByRole('button', { name: 'More', exact: true }).click()
    await expect(page.getByRole('menuitem', { name: 'Add a custom engine', exact: true })).toBeVisible()
    await page.keyboard.press('Escape')
  }
  await expect(page.getByText('Web address', { exact: true })).toBeVisible()
  await expect(page.getByText('Who can open this page', { exact: true })).toBeVisible()
})

test('Save only leaves a Restart button for the running engine', async ({ page }) => {
  await open(page, '/status/engines/smoke')
  const header = page.locator('header')
  await expect(header.getByRole('button', { name: 'Restart', exact: true })).toHaveCount(0)
  await page.locator('#cfg_engines_smoke_startup_timeout').fill('61')
  await save(page, 'Save only')
  await expect(header.getByLabel('Saved changes wait for a restart')).toBeVisible()
  await expect(header.getByRole('button', { name: 'Restart', exact: true })).toBeVisible()
})

test('the brain model lists running and downloaded models, and a model not on disk costs a download', async ({ page }) => {
  // The brain as running, so a model change restarts it, and a cache with one quant it can load and one it cannot.
  const running = { name: 'llm', kind: 'engine', driver: 'docker', state: 'healthy', detail: '', since: 0, port: 8000,
    startup_timeout: 900, started_in: 12, config_changed: false }
  await page.route('**/api/status/stream', (route) => route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }))
  await page.route('**/api/status', async (route) => {
    const status = (await (await route.fetch()).json()) as { engines: unknown[] }
    await route.fulfill({ json: { ...status, engines: [...status.engines, running] } })
  })
  // The copy's brain is switched off, so winstond would not name it among the restarts.
  await page.route('**/api/config?*dry_run=true*', async (route) => {
    const result = (await (await route.fetch()).json()) as { restarts: string[] }
    await route.fulfill({ json: { ...result, restarts: [...new Set([...result.restarts, 'llm'])] } })
  })
  await page.route('**/api/options/llm_models?*', (route) =>
    route.fulfill({
      json: {
        name: 'llm_models', ok: true, source: 'fake', served_by: 'llamacpp',
        choices: [
          { value: 'smoke/Running-GGUF:Q4_K_M', label: 'smoke/Running-GGUF:Q4_K_M', hint: '5.1 GB', group: 'Running' },
          { value: 'smoke/Other-GGUF:Q8_0', label: 'smoke/Other-GGUF:Q8_0', hint: '9.0 GB', group: 'Downloaded' },
          { value: 'smoke/Safe-AWQ', label: 'smoke/Safe-AWQ', hint: '6.2 GB, vLLM only', group: 'Downloaded', disabled: true },
        ],
      },
    }),
  )
  const errors = watchErrors(page)
  await open(page, '/brain')
  const header = page.locator('header')
  const dialog = page.getByRole('alertdialog')

  await page.locator('#cfg_llm_model').click()
  await expect(page.getByRole('group', { name: 'Running' })).toContainText('smoke/Running-GGUF:Q4_K_M')
  await expect(page.getByRole('group', { name: 'Downloaded' })).toContainText('9.0 GB')
  await expect(page.getByRole('option', { name: /smoke\/Safe-AWQ/ })).toHaveAttribute('aria-disabled', 'true')

  // On disk: the restart costs what it did last time.
  await page.getByRole('option', { name: /smoke\/Other-GGUF:Q8_0/ }).click()
  await header.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(dialog).toContainText('Brain: restarts, about 12 s')
  await dialog.getByRole('button', { name: 'Cancel' }).click()

  // Not on disk: the engine downloads it first, and the size is not known without asking the Hub.
  await page.locator('#cfg_llm_model').click()
  await page.getByPlaceholder('Search').fill('smoke/New-GGUF:Q4_K_M')
  await page.getByRole('option', { name: 'Use smoke/New-GGUF:Q4_K_M' }).click()
  await header.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(dialog).toContainText('Brain: restarts, downloads the model first, size unknown, so it may take many minutes')
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await page.getByRole('button', { name: 'Discard', exact: true }).click()
  expect(errors).toEqual([])
})

test('the Voice page plays the unsaved voice and its streaming, and not an unsaved engine', async ({ page }) => {
  const bodies: Record<string, unknown>[] = []
  await page.route('**/api/speak', async (route) => {
    bodies.push(route.request().postDataJSON() as Record<string, unknown>)
    // A tenth of a second of silence, as the speech server would stream it.
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'audio/pcm', 'x-sample-rate': '24000', 'x-streaming': 'on' },
      body: Buffer.alloc(4800),
    })
  })
  // Speaking as running: the copy's engines are switched off, and a voice is heard only from a running one.
  const running = { name: 'tts', kind: 'engine', driver: 'subprocess', state: 'healthy', detail: '', since: 0, port: 9,
    startup_timeout: 120, started_in: 1, config_changed: false }
  await page.route('**/api/status/stream', (route) => route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }))
  await page.route('**/api/status', async (route) => {
    const status = (await (await route.fetch()).json()) as { engines: { name: string }[] }
    await route.fulfill({ json: { ...status, engines: [...status.engines.filter((e) => e.name !== 'tts'), running] } })
  })
  await page.route('**/api/config', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const config = (await (await route.fetch()).json()) as { engines: Record<string, { enabled?: boolean }> }
    await route.fulfill({ json: { ...config, engines: { ...config.engines, tts: { ...config.engines.tts, enabled: true } } } })
  })
  const errors = watchErrors(page)
  await open(page, '/voice')
  const play = page.getByRole('button', { name: 'Hear this voice' })
  await expect(play).toBeEnabled()

  // An unsaved voice, typed in, is the one spoken.
  await page.locator('#cfg_tts_voice').click()
  await page.getByPlaceholder('Search').fill('smoke/unsaved_voice')
  await page.getByRole('option', { name: 'Use smoke/unsaved_voice' }).click()
  await play.click()
  await expect.poll(() => bodies.length).toBe(1)
  expect(bodies[0].voice).toBe('smoke/unsaved_voice')
  expect(String(bodies[0].text)).toMatch(/^Hello, I'm .+\. This is how I sound\.$/)

  // The sample carries the draft's streaming and shows its time to first sound. Streaming settings are
  // drawn only for a speech engine whose command takes them ({tts.streaming.*}); the copy's may not.
  const streams = readFileSync(join(data, 'config.yaml'), 'utf8').includes('{tts.streaming.')
  if (streams) {
    const sample = page.getByRole('button', { name: 'Play a sample' })
    await expect(sample).toBeEnabled()
    await sample.click()
    await expect.poll(() => bodies.length).toBe(2)
    expect(bodies[1].voice).toBe('smoke/unsaved_voice')
    expect(bodies[1].streaming).toMatchObject({ chunk_secs: expect.any(Number) as unknown })
    await expect(page.getByText(/^\d+\.\d\d s to first sound$/)).toBeVisible()
  } else {
    test.info().annotations.push({ type: 'skipped part', description: 'the speech engine takes no streaming settings' })
    await expect(page.getByRole('button', { name: 'Play a sample' })).toHaveCount(0)
  }

  // Another engine in the draft is not the one running: both wait for a save and restart.
  await page.locator('#tts-engine').click()
  await page.getByRole('option', { selected: false }).filter({ hasNotText: 'Custom' }).first().click()
  await expect(play).toBeDisabled()
  expect(bodies).toHaveLength(streams ? 2 : 1)
  expect(errors).toEqual([])
})

test('the Hearing card shows his hint words, removes his and saves yours', async ({ page }) => {
  const errors = watchErrors(page)
  await open(page, '/voice')
  const name = String(/^ {2}name: (.+)$/m.exec(readFileSync(join(data, 'config.yaml'), 'utf8'))?.[1] ?? 'Winston')
  const words = page.locator('#cfg_stt_hint_words')
  // His words are folded by where they come from; his name is among his own.
  await words.getByRole('button', { name: /^His name and voices/ }).click()
  await expect(words.getByText(name, { exact: true })).toBeVisible()
  await words.getByRole('button', { name: `Remove ${name}` }).click()
  await save(page)
  const removed = new RegExp(`hint_words_removed:\\n\\s*- ${name}`)
  await expect.poll(() => removed.test(readFileSync(join(data, 'config.yaml'), 'utf8'))).toBe(true)
  await words.getByRole('button', { name: `Add back ${name}` }).click()
  await save(page)
  await expect.poll(() => removed.test(readFileSync(join(data, 'config.yaml'), 'utf8'))).toBe(false)
  const add = page.locator('#cfg_stt_hint_words-add')
  await add.fill(name.toLowerCase())
  await expect(words.getByText('Already in the list')).toBeVisible()
  await add.fill('Vardagsrummet')
  await add.press('Enter')
  await expect(words.getByRole('button', { name: 'Remove Vardagsrummet' })).toBeVisible()
  await expect(page.locator('header').getByRole('button', { name: 'Save', exact: true })).toBeEnabled()
  await save(page)
  await expect.poll(() => /hint_words:\n\s*- Vardagsrummet/.test(readFileSync(join(data, 'config.yaml'), 'utf8'))).toBe(true)
  await page.reload()
  await words.getByRole('button', { name: 'Remove Vardagsrummet' }).click()
  await save(page)
  await expect.poll(() => readFileSync(join(data, 'config.yaml'), 'utf8').includes('Vardagsrummet')).toBe(false)
  expect(errors).toEqual([])
})

test('the Your voices card shows the voice match in words that open its details', async ({ page }) => {
  const errors = watchErrors(page)
  // two clones in the throwaway copy: one checked as the same voice, one from before the check
  const friends = join(data, 'voices', 'friends')
  mkdirSync(friends, { recursive: true })
  const wav = Buffer.alloc(44 + 3200)
  wav.write('RIFF', 0); wav.writeUInt32LE(36 + 3200, 4); wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16)
  wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28)
  wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(3200, 40)
  const clone = (slug: string, extra: object) => {
    writeFileSync(join(friends, `${slug}.wav`), wav)
    writeFileSync(join(friends, `${slug}.json`), JSON.stringify({ slug, name: slug[0].toUpperCase() + slug.slice(1),
      created: 1790150000, consent_text: `I'm ${slug}, and I agree.`, seconds: 15, ...extra }))
  }
  clone('smokeerik', { consent_score: 0.82, consent_checked_at: 1790150000, consent_model: 'Wespeaker/wespeaker-voxceleb-resnet34' })
  clone('smokemaria', {})
  try {
    await open(page, '/voice')
    const same = page.getByRole('button', { name: 'Checked', exact: true })
    await expect(same).toBeVisible()
    await expect(page.getByRole('button', { name: 'Not checked' })).toBeVisible()
    await same.hover()
    await expect(page.getByRole('dialog').getByText('Score 0.82, 0.55 needed')).toBeVisible()
  } finally {
    for (const f of ['smokeerik.wav', 'smokeerik.json', 'smokemaria.wav', 'smokemaria.json']) rmSync(join(friends, f), { force: true })
  }
  expect(errors).toEqual([])
})

test('the Home Assistant page shows the list by room and saves aliases only', async ({ page }) => {
  const fake = process.env.FAKE_HA_URL
  test.skip(!fake, 'the fake Home Assistant did not start')
  const errors = watchErrors(page)
  await open(page, '/home-assistant')

  // Home Assistant's rooms, the name he hears over Home Assistant's own, and a word only for what is not
  // applied yet.
  const living = page.getByRole('group', { name: 'Living room' })
  await expect(living.getByText('Ceiling lamp', { exact: true })).toBeVisible()
  await expect(living.getByText('In Home Assistant: Taklampa')).toBeVisible()
  await expect(living.getByText('Names not added yet')).toBeVisible()
  const noRoom = page.getByRole('group', { name: 'No room' })
  await expect(noRoom.getByText('Phone battery', { exact: true })).toBeVisible()
  await expect(noRoom.getByText('Not exposed yet')).toBeVisible()
  await expect(page.getByText(/^Also shared by kind, every one of them: People/)).toBeVisible()
  await expect(page.getByText('Turn things on and off')).toBeVisible()
  await expect(page.getByText(/^Home Assistant does not match this list yet/)).toBeVisible()
  // What Home Assistant exposes beyond the list is named, with the way to keep it.
  await expect(page.getByRole('button', { name: 'Add them to the list' })).toBeVisible()

  // The picker hides what is listed and, until asked, settings and diagnostics; it adds with Home
  // Assistant's name.
  await page.getByRole('button', { name: 'Add from Home Assistant' }).click()
  const picker = page.getByRole('dialog')
  await expect(picker.getByText('Taklampa')).toHaveCount(0)
  await picker.getByLabel('Show 1 hidden (settings and diagnostics)').click()
  await expect(picker.getByText('Taklampa signal')).toBeVisible()
  await picker.getByRole('group', { name: 'Kitchen' }).getByText('Köksspotlights').click()
  await picker.getByRole('button', { name: 'Add 1' }).click()
  await expect(picker).toHaveCount(0)
  const kitchen = page.getByRole('group', { name: 'Kitchen' })
  await expect(kitchen.getByText('Köksspotlights')).toBeVisible()
  await expect(kitchen.getByText('Waiting for Save')).toBeVisible()

  // Search filters by room.
  await page.getByRole('searchbox', { name: 'Search names or rooms' }).fill('kitchen')
  await expect(page.getByRole('group', { name: 'Living room' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Clear search' }).click()

  const header = page.locator('header')
  await header.getByRole('button', { name: 'Save', exact: true }).click()
  // The confirm says what the save changes in Home Assistant, from a dry run against it.
  const dialog = page.getByRole('alertdialog')
  await expect(dialog.getByText(/^Home Assistant: exposes \d+/)).toBeVisible()
  await dialog.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText(/^Saved; .*exposed/)).toBeVisible()

  // Home Assistant got exposure and aliases, and never a new name.
  const writes = (await (await page.request.get(`${fake}/fake/log`)).json()) as Record<string, unknown>[]
  const updates = writes.filter((w) => w.type === 'config/entity_registry/update')
  expect(updates.length).toBeGreaterThan(0)
  for (const w of updates) expect(w).not.toHaveProperty('name')
  expect(writes.some((w) => w.type === 'homeassistant/expose_entity' && (w.entity_ids as string[]).includes('light.kok'))).toBe(true)

  // Applied: no row has a word left and the list matches.
  for (const word of ['Waiting for Save', 'Not exposed yet', 'Names not added yet']) await expect(page.getByText(word)).toHaveCount(0)
  await expect(page.getByText(/does not match this list/)).toHaveCount(0)
  expect(readFileSync(join(data, 'config.yaml'), 'utf8')).toContain('light.kok')
  expect(errors).toEqual([])
})

test('Memory shows the example topics, dated facts, and folds and clears the expired', async ({ page }) => {
  // The copy's memory is examples/memory (smoke.sh); two facts go in by hand: one far off, one past.
  const plans = join(data, 'memory', 'plans.md')
  writeFileSync(
    plans,
    readFileSync(plans, 'utf8') +
      '- The user sees the dentist on Monday 31 December 2099. (noted 2026-09-24, until 2099-12-31)\n' +
      '- The user had a haircut booked. (noted 2026-01-01, until 2026-01-02)\n',
  )
  const errors = watchErrors(page)
  await open(page, '/memory')
  for (const title of ['About you', 'Home', 'Plans', 'People']) {
    await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
  }
  // A topic's description is a line under its heading.
  await expect(page.getByRole('region', { name: 'About you' }).getByText('Likes, dislikes, habits and ways of doing things.')).toBeVisible()

  // The dated fact carries its day; the expired one is folded and out of the size line.
  const dentist = page.getByRole('region', { name: 'Plans' }).locator('div', { hasText: 'dentist' }).last()
  await expect(dentist.getByText(/^Until (31 Dec|Dec 31)$/)).toBeVisible()
  await expect(page.getByText('The user had a haircut booked.')).toHaveCount(0)
  await expect(page.getByText(/^Read in every conversation: /)).toBeVisible()
  await page.getByRole('button', { name: 'Expired (1)' }).click()
  await expect(page.getByText('The user had a haircut booked.')).toBeVisible()

  // Editing clears the date; the file keeps the fact without an until.
  await page.getByRole('button', { name: /^Actions for The user sees the dentist/ }).click()
  await page.getByRole('menuitem', { name: 'Edit' }).click()
  await page.getByLabel('Until', { exact: true }).fill('')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect.poll(() => readFileSync(plans, 'utf8')).toMatch(/dentist on Monday 31 December 2099\. \(noted \d{4}-\d{2}-\d{2}\)\n/)

  // Clear expired asks once, and Undo brings the fact back with its dates.
  await page.getByRole('button', { name: 'Clear expired' }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: 'Clear expired' }).click()
  await expect(page.getByRole('button', { name: /^Expired/ })).toHaveCount(0)
  expect(readFileSync(plans, 'utf8')).not.toContain('haircut')
  // The edit's toast offers an Undo too: take the one on the clearing's.
  await page.getByRole('listitem').filter({ hasText: 'Cleared 1 expired fact' }).getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByRole('button', { name: 'Expired (1)' })).toBeVisible()
  expect(readFileSync(plans, 'utf8')).toContain('The user had a haircut booked. (noted 2026-01-01, until 2026-01-02)')
  expect(errors).toEqual([])
})
