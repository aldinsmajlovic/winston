import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test } from '@playwright/test'

// Runs against the throwaway winstond `pnpm smoke` starts. Its copy of data/ has no turns of its own, so
// this writes one made-up turn into the copy's turn record and marks it from the page.
const data = process.env.WINSTON_DATA ?? ''

test.beforeAll(() => {
  if (!process.env.WINSTON_URL || !data) throw new Error('Run with `pnpm smoke`: it starts the throwaway winstond')
})

function today(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

test('the History page shows a day and marks a spoken request from it', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  const id = 'abcdef012345'
  const folder = join(data, 'turns', today())
  mkdirSync(folder, { recursive: true })
  const turn = {
    id, ts: Date.now() / 1000, channel: 'voice', room: null, start: 'speech', secs: 1.2, heard: 'Turn on the lamb.',
    engine: 'parakeet/test', stt_ms: 210, first_token_ms: 640, first_audio_ms: 1150,
    tools: [{ name: 'homeassistant__GetLiveContext', args: {}, ok: true, result: '{}' }], said: 'Which lamb, sir?',
    outcome: 'answered',
  }
  writeFileSync(join(folder, 'voice.jsonl'), JSON.stringify(turn) + '\n')

  await page.goto('/history')
  await expect(page.getByText('Turn on the lamb.')).toBeVisible()
  await expect(page.getByText('Checked the home')).toBeVisible()        // the tool as the live feed words it
  await expect(page.getByText('Which lamb, sir?')).toBeVisible()

  await page.getByText('Turn on the lamb.').hover()
  await page.getByRole('button', { name: 'Mark a mistake' }).click()
  await page.getByRole('menuitem', { name: 'He misheard me' }).click()
  await page.getByRole('textbox', { name: 'What you said' }).fill('Turn on the lamp.')
  await page.getByRole('button', { name: 'Mark', exact: true }).click()
  await expect(page.getByText('He misheard me. You said "Turn on the lamp."')).toBeVisible()
  expect(existsSync(join(data, 'turns', 'marked', `${id}.json`))).toBe(true)  // kept past its day

  await page.getByRole('button', { name: 'Marked (1)' }).click()
  await expect(page.getByText('Turn on the lamb.')).toBeVisible()
  await page.getByRole('button', { name: 'Marked: He misheard me' }).click()
  await page.getByRole('menuitem', { name: 'Remove mark' }).click()
  await expect(page.getByText('Nothing marked yet.')).toBeVisible()
  expect(existsSync(join(data, 'turns', 'marked', `${id}.json`))).toBe(false)
  expect(errors).toEqual([])
})

test('a spoken line on Talk gets a flag once its turn is kept, and marks it', async ({ page, request }) => {
  const id = 'fedcba543210'
  const folder = join(data, 'turns', today())
  mkdirSync(folder, { recursive: true })
  const turn = {
    id, ts: Date.now() / 1000, channel: 'satellite', room: 'kitchen', start: 'wake', secs: 1.4,
    heard: 'What time is it now?', engine: 'parakeet/test', stt_ms: 980, first_token_ms: 900, first_audio_ms: 1600,
    tools: [], said: 'It is half past nine, sir.', outcome: 'answered',
  }
  writeFileSync(join(folder, 'satellite.jsonl'), JSON.stringify(turn) + '\n')
  // What the box's channel sends: what it heard, his reply, then the kept turn.
  await request.post('/api/live', {
    data: [
      { channel: 'satellite', type: 'user', text: turn.heard, room: 'kitchen' },
      { channel: 'satellite', type: 'reply', reply: 'r-talk', text: turn.said },
      { channel: 'satellite', type: 'reply_end', reply: 'r-talk' },
      { channel: 'satellite', type: 'turn', call: id, text: turn.heard, room: 'kitchen' },
    ],
  })
  await page.goto('/')
  await page.getByText(turn.heard).hover()
  await page.getByRole('button', { name: 'Mark a mistake' }).click()
  await page.getByRole('menuitem', { name: 'He was too slow' }).click()
  await expect(page.getByRole('button', { name: 'Marked: He was too slow' })).toBeVisible()
  expect(existsSync(join(data, 'turns', 'marked', `${id}.json`))).toBe(true)
})
