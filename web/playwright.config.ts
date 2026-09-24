import { defineConfig } from '@playwright/test'

// The settings smoke test (tests/). Run it with `pnpm smoke`, which starts a throwaway winstond on a
// copy of data/ and sets WINSTON_URL and WINSTON_DATA; it never touches the running one.
export default defineConfig({
  testDir: 'tests',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: process.env.WINSTON_URL,
    viewport: { width: 1400, height: 860 },
    // The system browser, so no browser download is needed.
    launchOptions: { executablePath: process.env.CHROMIUM ?? '/usr/bin/chromium' },
  },
})
