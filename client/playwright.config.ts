import { defineConfig, devices } from '@playwright/test'

/**
 * Browser end-to-end tests on a stack of their own, so they never touch the dev data:
 *
 * - a backend on :8001 using the database `<dev database>_e2e` (e.g. hotel_e2e), created,
 *   migrated and freshly seeded on every run (server/app/scripts/e2e_server.py);
 * - a Vite dev server on :5174 proxying to it.
 *
 * The dev stack (:8000 / :5173, database `hotel`) can keep running alongside. Needs the
 * database container. Run with `npm run test:e2e`.
 */
const CLIENT = 'http://localhost:5174'
const BACKEND = 'http://127.0.0.1:8001'

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
    baseURL: CLIENT,
    trace: 'retain-on-failure',
    ...devices['Desktop Chrome'],
  },
  webServer: [
    {
      command: 'uv run python -m app.scripts.e2e_server',
      cwd: '../server',
      url: `${BACKEND}/api/health`,
      env: { E2E_CLIENT_ORIGIN: CLIENT },
      // Always a fresh backend and database: never reuse whatever is on the port.
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: 'pipe',
    },
    {
      command: 'npx vite --port 5174 --strictPort',
      url: CLIENT,
      env: { BACKEND_URL: BACKEND },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
})
