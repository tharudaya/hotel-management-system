import { QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { createMemoryRouter } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { vi } from 'vitest'

import { setUnauthorizedHandler } from '../api/client'
import { createQueryClient } from '../queryClient'
import { routes } from '../routes'

/** A canned response, or a promise of one (to hold a request pending). */
type Reply = [status: number, body?: unknown] | Promise<[status: number, body?: unknown]>

/**
 * Replace fetch with a fake backend keyed by "METHOD /path".
 * Unmatched requests fail the test loudly.
 */
export function mockApi(handlers: Record<string, () => Reply>) {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const key = `${init?.method ?? 'GET'} ${path}`
    const handler = handlers[key]
    if (!handler) throw new Error(`Unexpected request: ${key}`)
    const [status, body] = await handler()
    return new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** Render the real route table at `path`, with a fresh query cache. */
export function renderApp(path: string) {
  // The production 401 handler does a full-page redirect; route guards are what we test.
  setUnauthorizedHandler(() => undefined)
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  render(
    <QueryClientProvider client={createQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

export const FRONT_DESK_USER = {
  id: 1,
  username: 'frontdesk',
  full_name: 'Fran Desk',
  role: 'front_desk',
} as const
