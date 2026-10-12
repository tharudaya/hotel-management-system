import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { PermissionGroup, RoleDetail, RoleSummary, User } from '../../api/types'
import { HOTEL } from '../../test/fixtures'
import { ADMIN_USER, FRONT_DESK_USER, MANAGER_USER, mockApi, renderApp } from '../../test/renderApp'

// --- fixtures -----------------------------------------------------------------------------

const CATALOG: PermissionGroup[] = [
  {
    group: 'users',
    permissions: [
      { key: 'user.view', description: 'View staff accounts' },
      { key: 'user.manage', description: 'Create, edit and deactivate staff accounts' },
    ],
  },
  {
    group: 'dashboard',
    permissions: [{ key: 'dashboard.view', description: 'View the front desk dashboard' }],
  },
  {
    group: 'reservations',
    permissions: [
      { key: 'reservation.view', description: 'View reservations' },
      { key: 'reservation.check_in', description: 'Check guests in' },
    ],
  },
  {
    group: 'settings',
    permissions: [{ key: 'settings.manage', description: 'Change property settings' }],
  },
]

function summary(overrides: Partial<RoleSummary>): RoleSummary {
  return {
    id: 10,
    name: 'Night auditor',
    description: 'Overnight reports',
    is_system: false,
    is_admin: false,
    user_count: 1,
    permission_count: 2,
    can_edit: true,
    can_delete: false,
    can_duplicate: true,
    ...overrides,
  }
}

function role(overrides: Partial<RoleDetail> = {}): RoleDetail {
  return {
    ...summary({}),
    permissions: ['dashboard.view', 'reservation.view'],
    users: [{ id: 7, username: 'owl', full_name: 'Olle Owl', status: 'active' }],
    ...overrides,
  }
}

const ADMIN_ROLE = role({
  id: 1,
  name: 'Admin',
  description: 'Full access, including settings',
  is_system: true,
  is_admin: true,
  can_edit: false,
  permissions: CATALOG.flatMap((g) => g.permissions.map((p) => p.key)).sort(),
  users: [],
  user_count: 1,
})

const RECEPTIONIST_ROLE = role({
  id: 4,
  name: 'Receptionist',
  description: 'Day-to-day front desk work',
  is_system: true,
  permissions: ['dashboard.view', 'reservation.check_in', 'reservation.view'],
  users: [],
  user_count: 3,
})

const LIST: RoleSummary[] = [
  summary({ ...ADMIN_ROLE, permission_count: 7, can_duplicate: false }),
  summary({ ...RECEPTIONIST_ROLE, permission_count: 3 }),
  summary({}),
]

function api(me: User = MANAGER_USER, extra: Record<string, () => [number, unknown?]> = {}) {
  return mockApi({
    'GET /api/auth/me': () => [200, me],
    'GET /api/hotel': () => [200, HOTEL],
    'GET /api/admin/roles': () => [200, LIST],
    'GET /api/admin/roles/permissions': () => [200, CATALOG],
    'GET /api/admin/roles/10': () => [200, role()],
    'GET /api/admin/roles/1': () => [200, ADMIN_ROLE],
    'GET /api/admin/roles/4': () => [200, RECEPTIONIST_ROLE],
    ...extra,
  })
}

function bodyOf(fetchMock: ReturnType<typeof mockApi>, method: string): unknown {
  const call = fetchMock.mock.calls.find(([, init]) => init?.method === method)
  const body = call?.[1]?.body
  return typeof body === 'string' ? JSON.parse(body) : undefined
}

function writes(fetchMock: ReturnType<typeof mockApi>): string[] {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method !== undefined && init.method !== 'GET')
    .map(([path, init]) => `${init?.method ?? ''} ${typeof path === 'string' ? path : ''}`)
}

const permissionBox = (name: string) =>
  screen.getByRole('checkbox', { name: new RegExp(`^${name}`) })

// --- list -----------------------------------------------------------------------------------

describe('roles list', () => {
  it('shows each role with its type, users and permissions', async () => {
    api()
    renderApp('/admin/roles')

    const table = await screen.findByRole('table', { name: 'Roles' })
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((th) => th.textContent),
    ).toEqual(['Role', 'Type', 'Users', 'Permissions', 'Actions'])
    const admin = within(table).getByRole('row', { name: /^Admin/ })
    expect(admin).toHaveTextContent('System, locked')
    const desk = within(table).getByRole('row', { name: /^Receptionist/ })
    expect(
      within(desk)
        .getAllByRole('cell')
        .map((td) => td.textContent),
    ).toEqual(['System', '3', '3', 'Duplicate'])
    const night = within(table).getByRole('row', { name: /^Night auditor/ })
    expect(night).toHaveTextContent('Custom')
    expect(within(night).getByRole('link', { name: 'Night auditor' })).toHaveAttribute(
      'href',
      '/admin/roles/10',
    )
    expect(screen.getByRole('link', { name: 'Create role' })).toBeInTheDocument()
  })

  it('hides Create and Duplicate without role.manage', async () => {
    api({ ...FRONT_DESK_USER, permissions: [...FRONT_DESK_USER.permissions, 'role.view'] })
    renderApp('/admin/roles')

    await screen.findByRole('table')
    expect(screen.queryByRole('link', { name: 'Create role' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Duplicate/ })).not.toBeInTheDocument()
  })

  it('duplicates a role under a new name', async () => {
    const copy = role({ id: 11, name: 'Senior receptionist', users: [], user_count: 0 })
    const fetchMock = api(MANAGER_USER, {
      'POST /api/admin/roles': () => [201, copy],
      'GET /api/admin/roles/11': () => [200, copy],
    })
    const router = renderApp('/admin/roles')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Duplicate Receptionist' }))
    const dialog = screen.getByRole('dialog', { name: 'Duplicate Receptionist' })
    const name = within(dialog).getByLabelText('Name of the new role')
    expect(name).toHaveValue('Copy of Receptionist')
    await user.clear(name)
    await user.type(name, 'Senior receptionist')
    await user.click(within(dialog).getByRole('button', { name: 'Create copy' }))

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/admin/roles/11')
    })
    expect(bodyOf(fetchMock, 'POST')).toEqual({
      name: 'Senior receptionist',
      copy_from_role_id: 4,
    })
    expect(
      await screen.findByText('Senior receptionist was created as a copy of Receptionist.'),
    ).toBeInTheDocument()
  })

  it('explains why a role cannot be duplicated', async () => {
    api()
    renderApp('/admin/roles')

    const button = await screen.findByRole('button', { name: 'Duplicate Admin' })
    expect(button).toHaveAttribute('aria-disabled', 'true')
    expect(button).toHaveAccessibleDescription(
      "Admin has permissions you don't have, so you can't copy it.",
    )
  })
})

// --- editing permissions --------------------------------------------------------------------

describe('editing permissions', () => {
  it('adds a permission and saves after reviewing the summary', async () => {
    const saved = role({
      permissions: ['dashboard.view', 'reservation.check_in', 'reservation.view'],
    })
    const fetchMock = api(MANAGER_USER, { 'PATCH /api/admin/roles/10': () => [200, saved] })
    renderApp('/admin/roles/10')
    const user = userEvent.setup()

    const checkIn = await screen.findByRole('checkbox', { name: /^Check guests in/ })
    expect(checkIn).not.toBeChecked()
    await user.click(checkIn)
    expect(screen.getByText('You have unsaved changes.')).toBeInTheDocument()
    const save = screen.getByRole('button', { name: 'Save changes' })
    await user.click(save)

    const dialog = screen.getByRole('dialog', { name: 'Save changes to Night auditor?' })
    expect(within(dialog).getByText('Added (1)')).toBeInTheDocument()
    expect(within(dialog).getByRole('listitem')).toHaveTextContent('Check guests in')
    expect(within(dialog).queryByText(/^Removed/)).not.toBeInTheDocument()
    expect(dialog).toHaveTextContent('1 user has this role; their access changes right away.')
    await user.click(within(dialog).getByRole('button', { name: 'Save changes' }))

    expect(await screen.findByText('Changes saved.')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(bodyOf(fetchMock, 'PATCH')).toEqual({
      permissions: ['dashboard.view', 'reservation.check_in', 'reservation.view'],
    })
    expect(save).toHaveFocus()
    expect(save).toHaveAttribute('aria-disabled', 'true') // nothing left to save
  })

  it('lists additions and removals; cancelling sends nothing', async () => {
    const fetchMock = api()
    renderApp('/admin/roles/10')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('checkbox', { name: /^View reservations/ }))
    await user.click(permissionBox('View staff accounts'))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Added (1)').nextElementSibling).toHaveTextContent(
      'View staff accounts',
    )
    expect(within(dialog).getByText('Removed (1)').nextElementSibling).toHaveTextContent(
      'View reservations',
    )
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save changes' })).toHaveFocus()
    expect(writes(fetchMock)).toEqual([])
  })

  it('selects a whole group, skipping permissions you cannot grant', async () => {
    api()
    renderApp('/admin/roles/10')
    const user = userEvent.setup()

    await user.click(
      await screen.findByRole('checkbox', { name: 'Select all reservations permissions' }),
    )
    expect(permissionBox('Check guests in')).toBeChecked()
    expect(permissionBox('View reservations')).toBeChecked()

    // The manager lacks settings.manage: no group toggle, and the box explains why.
    expect(
      screen.queryByRole('checkbox', { name: 'Select all settings permissions' }),
    ).not.toBeInTheDocument()
    const settings = permissionBox('Change property settings')
    expect(settings).toBeDisabled()
    expect(settings).toHaveAccessibleDescription(
      "You don't have this permission, so you can't grant it.",
    )

    // A partly selected group shows as mixed.
    await user.click(permissionBox('View reservations'))
    expect(
      screen.getByRole('checkbox', { name: 'Select all reservations permissions' }),
    ).toHaveAttribute('aria-checked', 'mixed')
  })

  it('filters permissions by text', async () => {
    api()
    renderApp('/admin/roles/10')
    const user = userEvent.setup()

    await user.type(await screen.findByRole('searchbox', { name: 'Filter permissions' }), 'check')

    expect(screen.getByText('1 permission shown')).toBeInTheDocument()
    expect(permissionBox('Check guests in')).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: /^View staff accounts/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Users' })).not.toBeInTheDocument()

    await user.clear(screen.getByRole('searchbox', { name: 'Filter permissions' }))
    await user.type(screen.getByRole('searchbox', { name: 'Filter permissions' }), 'zzz')
    expect(screen.getByText('No permissions match “zzz”.')).toBeInTheDocument()
  })

  it('renames a custom role, shown in the summary', async () => {
    const fetchMock = api(MANAGER_USER, {
      'PATCH /api/admin/roles/10': () => [200, role({ name: 'Night manager' })],
    })
    renderApp('/admin/roles/10')
    const user = userEvent.setup()

    const name = await screen.findByLabelText('Name')
    await user.clear(name)
    await user.type(name, 'Night manager')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('Rename from Night auditor to Night manager.')
    expect(dialog).not.toHaveTextContent('their access changes') // permissions unchanged
    await user.click(within(dialog).getByRole('button', { name: 'Save changes' }))

    await screen.findByText('Changes saved.')
    expect(bodyOf(fetchMock, 'PATCH')).toEqual({ name: 'Night manager' })
  })

  it('keeps the dialog open with the server error', async () => {
    api(MANAGER_USER, {
      'PATCH /api/admin/roles/10': () => [
        403,
        {
          detail: "You cannot grant permissions you don't have: user.manage.",
          code: 'permission_not_held',
        },
      ],
    })
    renderApp('/admin/roles/10')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('checkbox', { name: /^Check guests in/ }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Save changes' }),
    )

    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent(
      "You cannot grant permissions you don't have: user.manage.",
    )
  })

  it('has nothing to save until something changes', async () => {
    api()
    renderApp('/admin/roles/10')

    const save = await screen.findByRole('button', { name: 'Save changes' })
    expect(save).toHaveAttribute('aria-disabled', 'true')
    expect(save).toHaveAccessibleDescription('There are no changes to save.')
    expect(screen.getByRole('button', { name: 'Discard changes' })).toBeDisabled()
  })
})

// --- read-only roles --------------------------------------------------------------------

describe('read-only roles', () => {
  it('the Admin role is fully read-only', async () => {
    api(ADMIN_USER)
    renderApp('/admin/roles/1')

    expect(
      await screen.findByText('The Admin role always has every permission and cannot be changed.'),
    ).toBeInTheDocument()
    for (const box of screen.getAllByRole('checkbox')) {
      expect(box).toBeDisabled()
      expect(box).toBeChecked()
    }
    expect(screen.queryByRole('checkbox', { name: /^Select all/ })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Name')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Delete role' })).not.toBeInTheDocument()
  })

  it('system roles keep name and description; permissions can still change', async () => {
    const fetchMock = api(MANAGER_USER, {
      'PATCH /api/admin/roles/4': () => [200, RECEPTIONIST_ROLE],
    })
    renderApp('/admin/roles/4')
    const user = userEvent.setup()

    expect(
      await screen.findByText('This is a system role: its name and description cannot be changed.'),
    ).toBeInTheDocument()
    expect(screen.queryByLabelText('Name')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Delete role' })).not.toBeInTheDocument()

    await user.click(permissionBox('View staff accounts'))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(screen.getByRole('dialog')).toHaveTextContent(
      '3 users have this role; their access changes right away.',
    )
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Save changes' }),
    )

    await screen.findByText('Changes saved.')
    expect(bodyOf(fetchMock, 'PATCH')).toEqual({
      permissions: ['dashboard.view', 'reservation.check_in', 'reservation.view', 'user.view'],
    })
  })

  it('a role with permissions you lack is read-only, with the reason', async () => {
    api(MANAGER_USER, {
      'GET /api/admin/roles/10': () => [
        200,
        role({ permissions: ['settings.manage'], can_edit: false }),
      ],
    })
    renderApp('/admin/roles/10')

    expect(
      await screen.findByText("This role has permissions you don't have, so you can't change it."),
    ).toBeInTheDocument()
    expect(permissionBox('View staff accounts')).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument()
  })

  it('is read-only without role.manage', async () => {
    api({ ...FRONT_DESK_USER, permissions: [...FRONT_DESK_USER.permissions, 'role.view'] })
    renderApp('/admin/roles/10')

    expect(await screen.findByText('You can view this role but not change it.')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Delete role' })).not.toBeInTheDocument()
    // Without user.view, holders are plain names, not links.
    expect(screen.getByText('Olle Owl')).not.toHaveAttribute('href')
  })
})

// --- users and deletion -----------------------------------------------------------------

describe('users and deletion', () => {
  it('lists the users who have the role, linking to them', async () => {
    api()
    renderApp('/admin/roles/10')

    const section = await screen.findByRole('region', { name: 'Users with this role (1)' })
    expect(within(section).getByRole('link', { name: 'Olle Owl' })).toHaveAttribute(
      'href',
      '/admin/users/7',
    )
    expect(section).toHaveTextContent('Active')
  })

  it('blocks deleting a role that users still have', async () => {
    const fetchMock = api()
    renderApp('/admin/roles/10')
    const user = userEvent.setup()

    const remove = await screen.findByRole('button', { name: 'Delete Night auditor' })
    expect(remove).toHaveAttribute('aria-disabled', 'true')
    expect(remove).toHaveAccessibleDescription(
      '1 user still has this role. Give them another role first.',
    )
    await user.click(remove)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(writes(fetchMock)).toEqual([])
  })

  it('deletes an unused custom role after confirming', async () => {
    const fetchMock = api(MANAGER_USER, {
      'GET /api/admin/roles/10': () => [200, role({ users: [], user_count: 0, can_delete: true })],
      'DELETE /api/admin/roles/10': () => [204],
    })
    const router = renderApp('/admin/roles/10')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Delete Night auditor' }))
    const dialog = screen.getByRole('dialog', { name: 'Delete Night auditor?' })
    await user.click(within(dialog).getByRole('button', { name: 'Delete role' }))

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/admin/roles')
    })
    const notice = await screen.findByText('Night auditor was deleted.')
    expect(notice).toHaveFocus()
    expect(writes(fetchMock)).toEqual(['DELETE /api/admin/roles/10'])
  })
})

// --- create ---------------------------------------------------------------------------

describe('create role', () => {
  it('creates a role with chosen permissions', async () => {
    const created = role({ id: 12, name: 'Concierge', users: [], user_count: 0 })
    const fetchMock = api(MANAGER_USER, {
      'POST /api/admin/roles': () => [201, created],
      'GET /api/admin/roles/12': () => [200, created],
    })
    const router = renderApp('/admin/roles/new')
    const user = userEvent.setup()

    await user.type(await screen.findByLabelText('Name'), 'Concierge')
    await user.click(
      await screen.findByRole('checkbox', { name: /^View the front desk dashboard/ }),
    )
    await user.click(screen.getByRole('button', { name: 'Create role' }))

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/admin/roles/12')
    })
    expect(bodyOf(fetchMock, 'POST')).toEqual({
      name: 'Concierge',
      description: null,
      permissions: ['dashboard.view'],
    })
  })

  it('requires a name', async () => {
    const fetchMock = api()
    renderApp('/admin/roles/new')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Create role' }))

    expect(screen.getByLabelText('Name')).toHaveFocus()
    expect(screen.getByLabelText('Name')).toHaveAccessibleDescription('Enter a name for the role.')
    expect(writes(fetchMock)).toEqual([])
  })
})
