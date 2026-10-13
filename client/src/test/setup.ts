import '@testing-library/jest-dom/vitest'

import { cleanup, configure } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'

import { clearToasts } from '../components/toast'
import { FakeWebSocket } from './fakeWebSocket'

// findBy*/waitFor give up after 1 s by default; a busy machine running the whole suite
// in parallel can need longer for a page's first render. Passing tests are not slowed.
configure({ asyncUtilTimeout: 3000 })

beforeEach(() => {
  // Never open real sockets; tests that care drive FakeWebSocket.latest().
  FakeWebSocket.reset()
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

// Vitest globals are off, so React Testing Library cannot register this itself.
afterEach(() => {
  cleanup()
  clearToasts()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})
