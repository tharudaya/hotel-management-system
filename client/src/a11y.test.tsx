/**
 * Accessibility checks with axe-core on the real pages, plus keyboard paths.
 * (Color contrast cannot be computed in jsdom, so that one rule is off here.)
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { describe, expect, it } from 'vitest'

import { dashboardHandlers, HOTEL, makeReservation } from './test/fixtures'
import { ADMIN_USER, FRONT_DESK_USER, MANAGER_USER, mockApi, renderApp } from './test/renderApp'

const MANAGER = MANAGER_USER

async function expectNoViolations() {
  const results = await axe.run(document.body, {
    rules: { 'color-contrast': { enabled: false } },
  })
  const problems = results.violations.map(
    (v) =>
      `${v.id} (${v.impact ?? 'unknown'}): ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
  )
  expect(problems).toEqual([])
}

describe('accessibility (axe)', () => {
  it('login page, including the error state', async () => {
    mockApi({ 'POST /api/auth/login': () => [401, { detail: 'Invalid username or password' }] })
    renderApp('/login')
    await expectNoViolations()

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Username'), 'x')
    await user.type(screen.getByLabelText('Password'), 'y')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    await screen.findByText('Invalid username or password.')

    await expectNoViolations()
  })

  it('dashboard with summary, filters and room grid', async () => {
    mockApi({ 'GET /api/auth/me': () => [200, FRONT_DESK_USER], ...dashboardHandlers() })
    renderApp('/')
    await screen.findByRole('button', { name: /^Room 201,/ })
    await screen.findByRole('list', { name: "Today's summary" })

    await expectNoViolations()
  })

  it('dashboard with the detail panel open (manager actions)', async () => {
    mockApi({
      'GET /api/auth/me': () => [200, MANAGER],
      ...dashboardHandlers(),
      'GET /api/reservations/13': () => [200, makeReservation()],
    })
    renderApp('/?room=4')
    const panel = await screen.findByRole('complementary', { name: 'Room 201' })
    await within(panel).findByText('Alan Turing')

    await expectNoViolations()
  })

  it('activation page: form, validation errors and success', async () => {
    const token = 'x'.repeat(43)
    mockApi({
      ['POST /api/auth/activate/check']: () => [200, { username: 'newbie', full_name: 'New Bie' }],
      ['POST /api/auth/activate']: () => [200, { detail: 'ok' }],
    })
    renderApp(`/activate#${token}`)
    await screen.findByRole('heading', { name: 'Welcome, New Bie' })
    await expectNoViolations()

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('New password'), 'short')
    await user.click(screen.getByRole('button', { name: 'Activate account' }))
    await expectNoViolations()

    await user.clear(screen.getByLabelText('New password'))
    await user.type(screen.getByLabelText('New password'), 'Tricky-Ferret-Lamp')
    await user.type(screen.getByLabelText('Confirm new password'), 'Tricky-Ferret-Lamp')
    await user.click(screen.getByRole('button', { name: 'Activate account' }))
    await screen.findByRole('heading', { name: 'Your account is ready' })
    await expectNoViolations()
  })

  it('expired reset link', async () => {
    const token = 'x'.repeat(43)
    mockApi({
      ['POST /api/auth/reset-password/check']: () => [410, { detail: 'x', code: 'token_expired' }],
    })
    renderApp(`/reset-password#${token}`)
    await screen.findByText('This link has expired.')
    await expectNoViolations()
  })

  it('forgot password page, invalid input and confirmation', async () => {
    mockApi({ 'POST /api/auth/forgot-password': () => [202, { detail: 'Sent if it exists.' }] })
    renderApp('/forgot-password')
    await expectNoViolations()

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Email address'), 'nope')
    await user.click(screen.getByRole('button', { name: 'Send reset link' }))
    await expectNoViolations()

    await user.type(screen.getByLabelText('Email address'), '@hotel.example')
    await user.click(screen.getByRole('button', { name: 'Send reset link' }))
    await screen.findByRole('heading', { name: 'Check your email' })
    await expectNoViolations()
  })

  it('admin area with side navigation', async () => {
    mockApi({ 'GET /api/auth/me': () => [200, ADMIN_USER], ...dashboardHandlers() })
    renderApp('/admin/roles')
    await screen.findByRole('heading', { name: 'Roles' })

    await expectNoViolations()
    expect(screen.getByRole('link', { name: 'Roles' })).toHaveAttribute('aria-current', 'page')
  })

  it('no-access page', async () => {
    mockApi({
      'GET /api/auth/me': () => [200, FRONT_DESK_USER],
      'GET /api/hotel': () => [200, HOTEL],
    })
    renderApp('/admin')
    await screen.findByRole('heading', { name: 'You do not have access' })
    await expectNoViolations()
  })

  it('403 toast', async () => {
    mockApi({
      'GET /api/auth/me': () => [200, FRONT_DESK_USER],
      'GET /api/hotel': () => [200, HOTEL],
      'GET /api/dashboard/summary': () => [403, { detail: 'Insufficient permissions' }],
      'GET /api/dashboard/rooms': () => [403, { detail: 'Insufficient permissions' }],
    })
    renderApp('/')
    await screen.findByRole('button', { name: 'Dismiss' })
    await expectNoViolations()
  })

  it('users list, add user form with errors, user detail and a dialog', async () => {
    const roles = [{ id: 4, name: 'Receptionist', assignable: true }]
    const item = {
      id: 7,
      username: 'alice',
      full_name: 'Alice Andersson',
      email: null,
      job_title: null,
      employee_id: null,
      status: 'invited',
      role: { id: 4, name: 'Receptionist' },
      last_login_at: null,
      expires_at: null,
      created_at: '2026-01-01T09:00:00Z',
    }
    mockApi({
      'GET /api/auth/me': () => [200, ADMIN_USER],
      'GET /api/hotel': () => [200, HOTEL],
      'GET /api/admin/users/roles': () => [200, roles],
      'GET /api/admin/users?sort=name&page=1&page_size=25': () => [
        200,
        { items: [item], total: 1, page: 1, page_size: 25 },
      ],
      'GET /api/admin/users/7': () => [
        200,
        {
          ...item,
          phone: null,
          roles: [{ property: { id: 1, name: 'Hotel', time_zone: 'UTC' }, role: item.role }],
          deactivated_at: null,
          restrictions: { reset_password: 'alice has not activated their account yet.' },
        },
      ],
      'GET /api/admin/audit?target_type=user&target_id=7&limit=10': () => [
        200,
        { items: [], next_cursor: null },
      ],
    })
    const router = renderApp('/admin/users')
    await screen.findByRole('link', { name: 'Alice Andersson' })
    await expectNoViolations()

    await router.navigate('/admin/users/new')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Create user' }))
    await expectNoViolations()

    await router.navigate('/admin/users/7')
    await user.click(await screen.findByRole('button', { name: 'Deactivate' }))
    await screen.findByRole('dialog')
    await expectNoViolations()
  })

  it('roles list, role editor and the save summary', async () => {
    const catalog = [
      {
        group: 'reservations',
        permissions: [
          { key: 'reservation.view', description: 'View reservations' },
          { key: 'reservation.check_in', description: 'Check guests in' },
        ],
      },
      {
        group: 'settings',
        permissions: [{ key: 'settings.manage', description: 'Change settings' }],
      },
    ]
    const custom = {
      id: 10,
      name: 'Night auditor',
      description: null,
      is_system: false,
      is_admin: false,
      user_count: 1,
      permission_count: 1,
      can_edit: true,
      can_delete: false,
      can_duplicate: true,
    }
    mockApi({
      'GET /api/auth/me': () => [200, MANAGER_USER],
      'GET /api/hotel': () => [200, HOTEL],
      'GET /api/admin/roles': () => [200, [custom]],
      'GET /api/admin/roles/permissions': () => [200, catalog],
      'GET /api/admin/roles/10': () => [
        200,
        {
          ...custom,
          permissions: ['reservation.view'],
          users: [{ id: 7, username: 'owl', full_name: 'Olle Owl', status: 'active' }],
        },
      ],
    })
    const router = renderApp('/admin/roles')
    await screen.findByRole('table', { name: 'Roles' })
    await expectNoViolations()

    await router.navigate('/admin/roles/10')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('checkbox', { name: /^Check guests in/ }))
    await expectNoViolations()
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await screen.findByRole('dialog')
    await expectNoViolations()
  })

  it('audit log with an expanded row', async () => {
    mockApi({
      'GET /api/auth/me': () => [200, ADMIN_USER],
      'GET /api/hotel': () => [200, HOTEL],
      'GET /api/admin/audit?limit=50': () => [
        200,
        {
          items: [
            {
              id: 'change-1',
              source: 'change',
              occurred_at: '2026-10-12T08:30:00Z',
              action: 'user.updated',
              actor: { id: 2, username: 'manager', full_name: 'Morgan' },
              target_type: 'user',
              target_id: '7',
              target_label: 'Alice Andersson',
              before: { job_title: null },
              after: { job_title: 'Night audit' },
              details: null,
              ip: '10.0.0.5',
            },
          ],
          next_cursor: 'abc',
        },
      ],
    })
    renderApp('/admin/audit')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /^Details: Profile changed/ }))
    await screen.findByRole('table', { name: 'Changed fields' })
    await expectNoViolations()
  })

  it('room setup: rooms, the room form and room types', async () => {
    const std = {
      id: 1,
      code: 'STD',
      name: 'Standard Queen',
      max_occupancy: 2,
      bed_setup: '1 queen',
      description: null,
      is_active: true,
      active_room_count: 1,
      upcoming_reservations: 0,
      retire_blocked: 'Room type STD still has 1 active room.',
      created_at: '2026-01-01T09:00:00Z',
      updated_at: '2026-01-01T09:00:00Z',
    }
    const room = {
      id: 101,
      number: '101',
      floor: 1,
      room_type: { id: 1, code: 'STD', name: 'Standard Queen', is_active: true },
      notes: null,
      housekeeping_status: 'inspected',
      is_active: true,
      retired_at: null,
      retired_by: null,
      guest_in_house: true,
      upcoming_reservations: 0,
      retire_blocked: 'Room 101 has a guest checked in.',
      type_change_blocked: 'Room 101 has a guest checked in.',
      version: 1,
      created_at: '2026-01-01T09:00:00Z',
      updated_at: '2026-01-01T09:00:00Z',
    }
    mockApi({
      'GET /api/auth/me': () => [200, ADMIN_USER],
      'GET /api/hotel': () => [200, HOTEL],
      'GET /api/admin/rooms?status=active': () => [200, [room]],
      'GET /api/admin/rooms?status=all': () => [200, [room]],
      'GET /api/admin/room-types?include_retired=true': () => [200, [std]],
      'GET /api/admin/room-types': () => [200, [std]],
    })
    const router = renderApp('/admin/rooms')
    await screen.findByRole('table', { name: 'Rooms by floor' })
    await expectNoViolations()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Edit room 101' }))
    await screen.findByRole('dialog', { name: 'Edit room 101' })
    await expectNoViolations()
    await user.keyboard('{Escape}')

    await router.navigate('/admin/rooms/types')
    await screen.findByRole('table', { name: 'Room types' })
    await expectNoViolations()
  })

  it('not-found page', async () => {
    mockApi({})
    renderApp('/no/such/page')
    await screen.findByRole('heading', { name: 'Page not found' })

    await expectNoViolations()
  })
})

describe('keyboard', () => {
  it('login form is in a sensible tab order and submits with Enter', async () => {
    const fetchMock = mockApi({
      'POST /api/auth/login': () => [401, { detail: 'Invalid username or password' }],
    })
    renderApp('/login')
    const user = userEvent.setup()

    await user.tab()
    expect(screen.getByLabelText('Username')).toHaveFocus()
    await user.keyboard('frontdesk')
    await user.tab()
    expect(screen.getByLabelText('Password')).toHaveFocus()
    await user.keyboard('secret{Enter}')

    await screen.findByText('Invalid username or password.')
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('skip link moves focus past the header to the main content', async () => {
    mockApi({ 'GET /api/auth/me': () => [200, FRONT_DESK_USER], ...dashboardHandlers() })
    renderApp('/')
    await screen.findByRole('button', { name: /^Room 201,/ })
    const user = userEvent.setup()

    await user.tab()
    const skip = screen.getByRole('link', { name: 'Skip to main content' })
    expect(skip).toHaveFocus()
    await user.click(skip)

    await waitFor(() => {
      expect(screen.getByRole('main')).toHaveFocus()
    })
  })

  it('a room tile opens the panel with Enter and Escape returns to the tile', async () => {
    mockApi({
      'GET /api/auth/me': () => [200, FRONT_DESK_USER],
      ...dashboardHandlers(),
      'GET /api/reservations/13': () => [200, makeReservation()],
    })
    renderApp('/')
    const tile = await screen.findByRole('button', { name: /^Room 201,/ })
    const user = userEvent.setup()

    tile.focus()
    await user.keyboard('{Enter}')
    const panel = await screen.findByRole('complementary', { name: 'Room 201' })
    expect(within(panel).getByRole('heading', { name: 'Room 201' })).toHaveFocus()

    await user.keyboard('{Escape}')
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^Room 201,/ })).toHaveFocus()
    })
  })
})
