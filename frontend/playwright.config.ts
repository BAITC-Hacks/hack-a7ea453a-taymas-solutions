import { defineConfig } from '@playwright/test'

// Start the local Python API and Vite before running this real-data smoke.
export default defineConfig({
  testDir: './e2e', workers: 1, timeout: 30_000,
  use: { baseURL: process.env.COPILOT_UI_URL ?? 'http://127.0.0.1:5178', viewport: { width: 1440, height: 1000 }, screenshot: 'only-on-failure' },
})
