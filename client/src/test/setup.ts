import '@testing-library/jest-dom/vitest'

import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'

import { clearToasts } from '../components/toast'
import { FakeWebSocket } from './fakeWebSocket'

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
