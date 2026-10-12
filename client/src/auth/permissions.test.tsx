import { QueryClientProvider } from '@tanstack/react-query'
import { render, renderHook, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it } from 'vitest'

import type { User } from '../api/types'
import { createQueryClient, FORBIDDEN_MESSAGE } from '../queryClient'
import { FakeWebSocket } from '../test/fakeWebSocket'
import { dashboardHandlers, HOTEL } from '../test/fixtures'
import {
  ADMIN_USER,
  FRONT_DESK_USER,
  FRONT_OFFICE_MANAGER_USER,
  HOUSEKEEPING_SUPERVISOR_USER,
  MANAGER_USER,
  ROOM_ATTENDANT_USER,
  mockApi,
  renderApp,
} from '../test/renderApp'
import { Can } from './Can'
import { P } from './permissions'
import { usePermissions } from './usePermissions'

function withQueryClient({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={createQueryClient()}>{children}</QueryClientProvider>
}

/** Fake backend where /me answers with `user` (or each user in turn, last one repeating). */
function signedInAs(...users: User[]) {
  let calls = 0
  const fetchMock = mockApi({
    'GET /api/auth/me': () => {
      const user = users[Math.min(calls, users.length - 1)]
      calls += 1
      return [200, user]
    },
    ...dashboardHandlers(),
  })
  return {
    fetchMock,
    meCalls: () => fetchMock.mock.calls.filter(([path]) => path === '/api/auth/me').length,
  }
}

describe('usePermissions', () => {
  it('denies everything until /me has loaded, then answers from it', async () => {
    signedInAs(FRONT_OFFICE_MANAGER_USER)
    const { result } = renderHook(() => usePermissions(), { wrapper: withQueryClient })

    expect(result.current.isLoading).toBe(true)
    expect(result.current.can(P.DASHBOARD_VIEW)).toBe(false)

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })
    expect(result.current.can(P.USER_VIEW)).toBe(true)
    expect(result.current.can(P.ROLE_VIEW)).toBe(false)
    expect(result.current.canAny(P.ROLE_VIEW, P.AUDIT_VIEW)).toBe(false)
    expect(result.current.canAny(P.ROLE_VIEW, P.USER_VIEW)).toBe(true)
    expect(result.current.canAny()).toBe(false)
  })
})

describe('<Can>', () => {
  it('renders children only when allowed, otherwise the fallback', async () => {
    signedInAs(FRONT_DESK_USER)
    render(
      <>
        <Can permission={P.RESERVATION_CHECK_IN}>
          <p>check in</p>
        </Can>
        <Can permission={P.ROLE_MANAGE} fallback={<p>read only</p>}>
          <p>edit roles</p>
        </Can>
        <Can anyOf={[P.AUDIT_VIEW, P.GUEST_VIEW]}>
          <p>any of</p>
        </Can>
        <Can anyOf={[P.AUDIT_VIEW, P.USER_VIEW]}>
          <p>none of</p>
        </Can>
      </>,
      { wrapper: withQueryClient },
    )

    expect(await screen.findByText('check in')).toBeInTheDocument()
    expect(screen.getByText('read only')).toBeInTheDocument()
    expect(screen.getByText('any of')).toBeInTheDocument()
    expect(screen.queryByText('edit roles')).not.toBeInTheDocument()
    expect(screen.queryByText('none of')).not.toBeInTheDocument()
  })
})

describe('RequirePermission', () => {
  it('shows a no-access page, not the login page, for a missing permission', async () => {
    signedInAs(FRONT_OFFICE_MANAGER_USER) // user.view only
    const router = renderApp('/admin/audit')

    expect(
      await screen.findByRole('heading', { name: 'You do not have access' }),
    ).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/admin/audit')
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument() // still signed in
    expect(screen.getByRole('link', { name: 'Back to the dashboard' })).toHaveAttribute('href', '/')
  })

  it('renders the page with the permission', async () => {
    signedInAs(MANAGER_USER)
    renderApp('/admin/audit')

    expect(await screen.findByRole('heading', { name: 'Audit log' })).toBeInTheDocument()
  })

  it('guards the whole admin area', async () => {
    signedInAs(FRONT_DESK_USER)
    const router = renderApp('/admin')

    expect(
      await screen.findByRole('heading', { name: 'You do not have access' }),
    ).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/admin')
    expect(screen.queryByRole('navigation', { name: 'Admin' })).not.toBeInTheDocument()
  })

  it('opens the first section the user may see', async () => {
    signedInAs(FRONT_OFFICE_MANAGER_USER)
    const router = renderApp('/admin')

    expect(await screen.findByRole('heading', { name: 'Users' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/admin/users')
  })
})

describe('navigation per seeded role', () => {
  it.each([
    ['Admin', ADMIN_USER, ['Users', 'Roles', 'Audit log']],
    ['General manager', MANAGER_USER, ['Users', 'Roles', 'Audit log']],
    ['Front office manager', FRONT_OFFICE_MANAGER_USER, ['Users']],
    ['Receptionist', FRONT_DESK_USER, []],
    ['Housekeeping supervisor', HOUSEKEEPING_SUPERVISOR_USER, []],
    ['Room attendant', ROOM_ATTENDANT_USER, []],
  ])('%s', async (_role, user, sections) => {
    signedInAs(user)
    renderApp('/admin')
    await screen.findByRole('button', { name: 'Sign out' })

    const adminLink = screen.queryByRole('link', { name: 'Admin' })
    if (sections.length === 0) {
      expect(adminLink).not.toBeInTheDocument()
      expect(
        await screen.findByRole('heading', { name: 'You do not have access' }),
      ).toBeInTheDocument()
      return
    }
    expect(adminLink).toBeInTheDocument()
    const nav = await screen.findByRole('navigation', { name: 'Admin' })
    expect(
      within(nav)
        .getAllByRole('link')
        .map((link) => link.textContent),
    ).toEqual(sections)
    // The dashboard link follows dashboard.view (every seeded admin-capable role has it).
    expect(
      within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', {
        name: 'Dashboard',
      }),
    ).toBeInTheDocument()
  })
})

describe('permission changes', () => {
  it('a 403 shows a toast and refetches /me', async () => {
    const fetchMock = mockApi({
      'GET /api/auth/me': () => [200, FRONT_DESK_USER],
      'GET /api/hotel': () => [200, HOTEL],
      'GET /api/dashboard/summary': () => [403, { detail: 'Insufficient permissions' }],
      'GET /api/dashboard/rooms': () => [403, { detail: 'Insufficient permissions' }],
    })
    renderApp('/')

    const toasts = await screen.findByRole('status', { name: 'Notifications' })
    expect(await within(toasts).findByText(FORBIDDEN_MESSAGE)).toBeInTheDocument()
    // Two failing queries, one toast.
    expect(within(toasts).getAllByText(FORBIDDEN_MESSAGE)).toHaveLength(1)
    await waitFor(() => {
      expect(fetchMock.mock.calls.filter(([p]) => p === '/api/auth/me').length).toBeGreaterThan(1)
    })
  })

  it('the toast can be dismissed', async () => {
    mockApi({
      'GET /api/auth/me': () => [200, FRONT_DESK_USER],
      'GET /api/hotel': () => [200, HOTEL],
      'GET /api/dashboard/summary': () => [403, { detail: 'Insufficient permissions' }],
      'GET /api/dashboard/rooms': () => [403, { detail: 'Insufficient permissions' }],
    })
    renderApp('/')
    await screen.findByText(FORBIDDEN_MESSAGE)

    screen.getByRole('button', { name: 'Dismiss' }).click()

    await waitFor(() => {
      expect(screen.queryByText(FORBIDDEN_MESSAGE)).not.toBeInTheDocument()
    })
  })

  it('permissions_changed over the WebSocket refetches /me and updates the navigation', async () => {
    // A General manager whose role is then reduced to a receptionist's permissions.
    const { meCalls } = signedInAs(MANAGER_USER, {
      ...MANAGER_USER,
      permissions: FRONT_DESK_USER.permissions,
    })
    renderApp('/')
    expect(await screen.findByRole('link', { name: 'Admin' })).toBeInTheDocument()
    const socket = FakeWebSocket.latest()
    socket.hello(0)

    socket.receive({ type: 'permissions_changed', seq: 0, payload: {} })

    await waitFor(() => {
      expect(screen.queryByRole('link', { name: 'Admin' })).not.toBeInTheDocument()
    })
    expect(meCalls()).toBe(2)
  })
})
