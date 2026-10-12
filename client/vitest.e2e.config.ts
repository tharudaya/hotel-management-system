import { defineConfig } from 'vitest/config'

/**
 * End-to-end checks against running servers (backend + `npm run dev`), seeded with
 * dev data. Run with `npm run test:e2e`.
 */
export default defineConfig({
  test: {
    include: ['src/e2e/**/*.e2e.test.ts'],
    environment: 'node',
    testTimeout: 20_000,
  },
})
