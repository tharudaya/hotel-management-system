import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { loadEnv } from 'vite'
import { configDefaults, defineConfig } from 'vitest/config'

// The monorepo keeps a single .env at the repository root.
const envDir = path.resolve(import.meta.dirname, '..')

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, envDir, '')
  // `||`, not `??`: an empty BACKEND_URL should also fall back to the default.
  const backend = env.BACKEND_URL || 'http://localhost:8000'

  return {
    envDir,
    plugins: [react(), tailwindcss()],
    server: {
      // Docker on Windows/macOS: file events from the mounted source don't reach the
      // container, so poll (set by docker-compose.yml).
      ...(env.DEV_USE_POLLING === 'true' ? { watch: { usePolling: true, interval: 300 } } : {}),
      // The browser only ever talks to the Vite origin, so the session cookie is
      // first-party and no CORS is involved. Origin is passed through unchanged,
      // which the backend checks on WebSocket connect.
      proxy: {
        '/api': { target: backend },
        '/ws': { target: backend, ws: true },
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      css: false,
      // Needs running servers: `npm run test:e2e` (vitest.e2e.config.ts).
      exclude: [...configDefaults.exclude, 'src/e2e/**'],
    },
  }
})
