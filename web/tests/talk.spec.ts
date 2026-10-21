import { expect, test } from '@playwright/test'

// Runs against the throwaway winstond `pnpm smoke` starts. Its brain is off, so the live feed is faked here:
// a typed line and the thinking state that follows it.
const shots = process.env.WINSTON_SHOTS

test('Talk shows him thinking, crosses out Read aloud while off and asks before a new conversation', async ({ page }) => {
  await page.route('/api/live', (route) => {
    const ts = Date.now() / 1000
    const events = [
      { channel: 'web', type: 'user', id: 'u1', ts, text: 'What time is it?' },
      { channel: 'web', type: 'state', ts, state: 'thinking' },
    ]
    return route.fulfill({ contentType: 'text/event-stream', body: events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('') })
  })
  let startedOver = false
  await page.route('/api/chat/start-over', (route) => {
    startedOver = true
    return route.fulfill({ json: {} })
  })
  await page.goto('/')
  await expect(page.getByText('What time is it?')).toBeVisible()
  await expect(page.locator('main').getByText('Thinking', { exact: true })).toBeVisible()

  // Read aloud: the speaker crossed out while off, sound waves while on, and it stays put while pressed.
  const read = page.getByRole('button', { name: 'Read typed replies aloud' })
  const pressed = await read.getAttribute('aria-pressed')
  if (pressed === 'true') await read.click()
  await expect(read.locator('svg.lucide-volume-x')).toBeVisible()
  const box = (await read.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.waitForTimeout(300) // past the press transition
  expect(Math.abs((await read.boundingBox())!.y - box.y)).toBeLessThan(2)
  await page.mouse.up()
  await expect(read.locator('svg.lucide-volume-2')).toBeVisible()
  if (shots) await page.screenshot({ path: `${shots}/talk-thinking.png` })
  if (pressed !== 'true') await read.click()

  // New conversation asks first; Cancel leaves the conversation alone.
  await page.getByRole('button', { name: /^New conversation/ }).click()
  await expect(page.getByRole('alertdialog')).toContainText('Start a new conversation?')
  if (shots) await page.screenshot({ path: `${shots}/talk-new-conversation.png` })
  await page.getByRole('button', { name: 'Cancel' }).click()
  expect(startedOver).toBe(false)
  await page.getByRole('button', { name: /^New conversation/ }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: 'New conversation' }).click()
  await expect.poll(() => startedOver).toBe(true)
})
