import { QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { createMemoryRouter } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { vi } from 'vitest'

import { setUnauthorizedHandler } from '../api/client'
import type { User } from '../api/types'
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

// Users as /api/auth/me returns them, mirroring the default roles on the server.
const PROPERTY = { id: 1, name: 'Grand Hotel Stockholm', time_zone: 'Europe/Stockholm' }

const RECEPTIONIST_PERMISSIONS = [
  'dashboard.view',
  'guest.view',
  'guest.view_contact',
  'payment.view',
  'reservation.check_in',
  'reservation.check_out',
  'reservation.create',
  'reservation.modify',
  'reservation.view',
  'room.update_housekeeping_status',
]

const ALL_PERMISSIONS = [
  ...RECEPTIONIST_PERMISSIONS,
  'audit.view',
  'payment.refund',
  'report.view',
  'reservation.cancel',
  'role.manage',
  'role.view',
  'room.set_out_of_order',
  'user.manage',
  'user.view',
].sort()

export const FRONT_DESK_USER: User = {
  id: 1,
  username: 'frontdesk',
  full_name: 'Fran Desk',
  property: PROPERTY,
  role: { id: 4, name: 'Receptionist' },
  permissions: RECEPTIONIST_PERMISSIONS,
}

export const MANAGER_USER: User = {
  id: 2,
  username: 'manager',
  full_name: 'Morgan',
  property: PROPERTY,
  role: { id: 2, name: 'General manager' },
  permissions: ALL_PERMISSIONS,
}

export const HOUSEKEEPING_SUPERVISOR_USER: User = {
  id: 3,
  username: 'housekeeping',
  full_name: 'Harper Keeping',
  property: PROPERTY,
  role: { id: 5, name: 'Housekeeping supervisor' },
  permissions: ['dashboard.view', 'room.set_out_of_order', 'room.update_housekeeping_status'],
}

export const ROOM_ATTENDANT_USER: User = {
  id: 4,
  username: 'attendant',
  full_name: 'Ari Attendant',
  property: PROPERTY,
  role: { id: 6, name: 'Room attendant' },
  permissions: ['room.update_housekeeping_status'],
}

export const ADMIN_USER: User = {
  id: 5,
  username: 'admin',
  full_name: 'Ada Admin',
  property: PROPERTY,
  role: { id: 1, name: 'Admin' },
  permissions: [...ALL_PERMISSIONS, 'settings.manage'].sort(),
}

export const FRONT_OFFICE_MANAGER_USER: User = {
  id: 6,
  username: 'fomanager',
  full_name: 'Flo Office',
  property: PROPERTY,
  role: { id: 3, name: 'Front office manager' },
  permissions: [
    ...RECEPTIONIST_PERMISSIONS,
    'payment.refund',
    'report.view',
    'reservation.cancel',
    'room.set_out_of_order',
    'user.view',
  ].sort(),
}
