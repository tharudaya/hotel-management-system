import { defineConfig, devices } from '@playwright/test'

/**
 * Browser end-to-end tests against the running app: the backend (seeded with
 * `python -m app.scripts.seed --reset`) and `npm run dev` on :5173. Each test makes its
 * own room types and rooms through the admin API, so they do not depend on the seed's
 * rooms. Run with `npm run test:browser`.
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  // One at a time: tests sign in as the same dev users, and a new sign-in ends the
  // user's previous session.
  workers: 1,
  fullyParallel: false,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    trace: 'retain-on-failure',
    ...devices['Desktop Chrome'],
  },
})
