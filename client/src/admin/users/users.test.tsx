import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type {
  AssignableRole,
  AuditPage,
  UserDetail,
  UserListItem,
  UserListPage,
} from '../../api/types'
import { HOTEL } from '../../test/fixtures'
import {
  ADMIN_USER,
  FRONT_OFFICE_MANAGER_USER,
  MANAGER_USER,
  mockApi,
  renderApp,
} from '../../test/renderApp'
import { DEFAULT_PARAMS, toApiQuery, type UserListParams } from './params'

// --- fixtures -----------------------------------------------------------------------------

const ROLES: AssignableRole[] = [
  { id: 1, name: 'Admin', assignable: false },
  { id: 2, name: 'General manager', assignable: true },
  { id: 3, name: 'Front office manager', assignable: true },
  { id: 4, name: 'Receptionist', assignable: true },
  { id: 5, name: 'Housekeeping supervisor', assignable: true },
  { id: 6, name: 'Room attendant', assignable: true },
]

function listItem(id: number, overrides: Partial<UserListItem> = {}): UserListItem {
  return {
    id,
    username: `user${id}`,
    full_name: `User ${id}`,
    email: `user${id}@hotel.example`,
    job_title: null,
    employee_id: null,
    status: 'active',
    role: { id: 4, name: 'Receptionist' },
    last_login_at: '2026-10-12T12:05:00Z',
    expires_at: null,
    created_at: '2026-01-01T09:00:00Z',
    ...overrides,
  }
}

const ALICE = listItem(7, {
  username: 'alice',
  full_name: 'Alice Andersson',
  email: 'alice@hotel.example',
})
const BOB = listItem(8, {
  username: 'bob',
  full_name: 'Bob Berg',
  email: null,
  status: 'invited',
  role: { id: 6, name: 'Room attendant' },
  last_login_at: null,
})

function page(items: UserListItem[], total = items.length, pageNumber = 1): UserListPage {
  return { items, total, page: pageNumber, page_size: 25 }
}

function detail(overrides: Partial<UserDetail> = {}): UserDetail {
  return {
    id: 7,
    username: 'alice',
    full_name: 'Alice Andersson',
    email: 'alice@hotel.example',
    phone: null,
    job_title: 'Receptionist',
    employee_id: 'E-7',
    status: 'active',
    roles: [
      {
        property: { id: 1, name: 'Grand Hotel Stockholm', time_zone: 'Europe/Stockholm' },
        role: { id: 4, name: 'Receptionist' },
      },
    ],
    created_at: '2026-01-01T09:00:00Z',
    last_login_at: '2026-10-12T12:05:00Z',
    expires_at: null,
    deactivated_at: null,
    restrictions: {
      reactivate: 'alice is not deactivated.',
      send_invite: 'alice has already activated their account.',
    },
    ...overrides,
  }
}

const NO_AUDIT: AuditPage = { items: [], next_cursor: null }

const listKey = (changes: Partial<UserListParams> = {}) =>
  `GET /api/admin/users?${toApiQuery({ ...DEFAULT_PARAMS, ...changes })}`

const AUDIT_KEY = 'GET /api/admin/audit?target_type=user&target_id=7&limit=10'

function base(me = ADMIN_USER) {
  return {
    'GET /api/auth/me': () => [200, me] as [number, unknown],
    'GET /api/hotel': () => [200, HOTEL] as [number, unknown],
    'GET /api/admin/users/roles': () => [200, ROLES] as [number, unknown],
  }
}

function requested(fetchMock: ReturnType<typeof mockApi>, method = 'GET'): string[] {
  return fetchMock.mock.calls
    .filter(([, init]) => (init?.method ?? 'GET') === method)
    .map(([path]) => (typeof path === 'string' ? path : ''))
}

function bodyOf(fetchMock: ReturnType<typeof mockApi>, path: string): unknown {
  const call = fetchMock.mock.calls.find(([p, init]) => p === path && init?.method !== 'GET')
  const body = call?.[1]?.body
  return typeof body === 'string' ? JSON.parse(body) : undefined
}

// --- list -----------------------------------------------------------------------------------

describe('users list', () => {
  it('shows users in a table with status text, role and last login', async () => {
    mockApi({ ...base(), [listKey()]: () => [200, page([ALICE, BOB])] })
    renderApp('/admin/users')

    const table = await screen.findByRole('table', { name: /Users, sorted by name, ascending/ })
    const headers = within(table)
      .getAllByRole('columnheader')
      .map((th) => th.textContent)
    expect(headers).toEqual(['Name', 'Username', 'Email', 'Role', 'Status', 'Last login'])
    const aliceRow = within(table).getByRole('row', { name: /Alice Andersson/ })
    const bobRow = within(table).getByRole('row', { name: /Bob Berg/ })
    expect(within(aliceRow).getByRole('rowheader')).toHaveTextContent('Alice Andersson')
    expect(within(aliceRow).getByRole('link', { name: 'Alice Andersson' })).toHaveAttribute(
      'href',
      '/admin/users/7',
    )
    // Hotel time (Stockholm, UTC+2 in October).
    expect(aliceRow).toHaveTextContent('12 Oct 2026, 14:05')
    expect(aliceRow).toHaveTextContent('Active')
    expect(bobRow).toHaveTextContent('Invited')
    expect(bobRow).toHaveTextContent('Room attendant')
    expect(bobRow).toHaveTextContent('Never')
    expect(screen.getByRole('status', { name: '' })).toHaveTextContent('2 users')
    expect(screen.getByRole('columnheader', { name: /Name/ })).toHaveAttribute(
      'aria-sort',
      'ascending',
    )
  })

  it('offers "Add user" only with user.manage', async () => {
    mockApi({ ...base(FRONT_OFFICE_MANAGER_USER), [listKey()]: () => [200, page([ALICE])] })
    renderApp('/admin/users')

    await screen.findByRole('table')
    expect(screen.queryByRole('link', { name: 'Add user' })).not.toBeInTheDocument()
  })

  it('shows loading, then an error with a retry', async () => {
    let calls = 0
    mockApi({
      ...base(),
      // The first try and both automatic retries fail; the manual retry works.
      [listKey()]: () => (++calls <= 3 ? [500, { detail: 'boom' }] : [200, page([ALICE])]),
    })
    renderApp('/admin/users')

    expect(screen.getByRole('status')).toHaveTextContent(/Loading|^$/)
    // A 500 is retried (with backoff) before the error shows.
    const alert = await screen.findByRole('alert', {}, { timeout: 5000 })
    expect(alert).toHaveTextContent('The users could not be loaded.')
    await userEvent.setup().click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('link', { name: 'Alice Andersson' })).toBeInTheDocument()
  }, 10_000)

  it('reads search, filters, sort and page from the URL', async () => {
    const fetchMock = mockApi({
      ...base(),
      [listKey({ q: 'bob', roleId: 6, status: 'invited', sort: '-email', page: 2 })]: () => [
        200,
        page([BOB], 26, 2),
      ],
    })
    renderApp('/admin/users?q=bob&role=6&status=invited&sort=-email&page=2')

    await screen.findByRole('link', { name: 'Bob Berg' })
    expect(screen.getByRole('searchbox', { name: 'Search' })).toHaveValue('bob')
    await waitFor(() => {
      expect(screen.getByRole('combobox', { name: 'Role' })).toHaveValue('6')
    })
    expect(screen.getByRole('combobox', { name: 'Status' })).toHaveValue('invited')
    expect(screen.getByRole('columnheader', { name: /Email/ })).toHaveAttribute(
      'aria-sort',
      'descending',
    )
    expect(screen.getByText('Page 2 of 2')).toBeInTheDocument()
    expect(requested(fetchMock)).toContain(
      '/api/admin/users?sort=-email&page=2&page_size=25&q=bob&role_id=6&status=invited',
    )
  })

  it('debounces the search box into the URL and the request', async () => {
    const fetchMock = mockApi({
      ...base(),
      [listKey()]: () => [200, page([ALICE, BOB])],
      [listKey({ q: 'ali' })]: () => [200, page([ALICE])],
    })
    const router = renderApp('/admin/users')
    await screen.findByRole('link', { name: 'Bob Berg' })

    // Pasted in one go (key-by-key typing can outlast the debounce on a busy machine).
    const user = userEvent.setup()
    await user.click(screen.getByRole('searchbox', { name: 'Search' }))
    await user.paste('ali')

    await waitFor(() => {
      expect(router.state.location.search).toBe('?q=ali')
    })
    await waitFor(() => {
      expect(screen.queryByRole('link', { name: 'Bob Berg' })).not.toBeInTheDocument()
    })
    // One request for the finished word, none per keystroke.
    expect(requested(fetchMock).filter((p) => p.includes('q='))).toEqual([
      '/api/admin/users?sort=name&page=1&page_size=25&q=ali',
    ])
  })

  it('filters by role and status with the keyboard', async () => {
    mockApi({
      ...base(),
      [listKey()]: () => [200, page([ALICE, BOB])],
      [listKey({ roleId: 6 })]: () => [200, page([BOB])],
      [listKey({ roleId: 6, status: 'invited' })]: () => [200, page([BOB])],
    })
    const router = renderApp('/admin/users')
    await screen.findByRole('link', { name: 'Alice Andersson' })
    const user = userEvent.setup()

    await user.selectOptions(screen.getByRole('combobox', { name: 'Role' }), 'Room attendant')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Status' }), 'Invited')

    await waitFor(() => {
      expect(router.state.location.search).toBe('?role=6&status=invited')
    })
    expect(screen.queryByRole('link', { name: 'Alice Andersson' })).not.toBeInTheDocument()
  })

  it('shows an empty state that clears the filters', async () => {
    mockApi({
      ...base(),
      [listKey({ status: 'expired' })]: () => [200, page([])],
      [listKey()]: () => [200, page([ALICE])],
    })
    const router = renderApp('/admin/users?status=expired')

    expect(await screen.findByText('No users match these filters.')).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Clear filters' }))

    expect(await screen.findByRole('link', { name: 'Alice Andersson' })).toBeInTheDocument()
    expect(router.state.location.search).toBe('')
  })

  it('sorts by clicking column headers, toggling the direction', async () => {
    mockApi({
      ...base(),
      [listKey()]: () => [200, page([ALICE, BOB])],
      [listKey({ sort: '-name' })]: () => [200, page([BOB, ALICE])],
      [listKey({ sort: 'last_login_at' })]: () => [200, page([ALICE, BOB])],
    })
    const router = renderApp('/admin/users')
    await screen.findByRole('table')
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Name' }))
    await waitFor(() => {
      expect(screen.getByRole('columnheader', { name: /Name/ })).toHaveAttribute(
        'aria-sort',
        'descending',
      )
    })
    expect(router.state.location.search).toBe('?sort=-name')

    await user.click(screen.getByRole('button', { name: 'Last login' }))
    await waitFor(() => {
      expect(router.state.location.search).toBe('?sort=last_login_at')
    })
  })

  it('paginates', async () => {
    const many = Array.from({ length: 25 }, (_, i) => listItem(100 + i))
    mockApi({
      ...base(),
      [listKey()]: () => [200, page(many, 30)],
      [listKey({ page: 2 })]: () => [200, page([ALICE], 30, 2)],
    })
    const router = renderApp('/admin/users')
    const pagination = await screen.findByRole('navigation', { name: 'Pagination' })
    expect(within(pagination).getByRole('button', { name: 'Previous' })).toBeDisabled()

    await userEvent.setup().click(within(pagination).getByRole('button', { name: 'Next' }))

    expect(await screen.findByRole('link', { name: 'Alice Andersson' })).toBeInTheDocument()
    expect(router.state.location.search).toBe('?page=2')
    expect(screen.getByText('Page 2 of 2')).toBeInTheDocument()
  })
})

// --- create -------------------------------------------------------------------------------

describe('add user', () => {
  it('creates a user, then shows an activation link when there is no email', async () => {
    const created = detail({
      id: 9,
      username: 'carl',
      full_name: 'Carl Carlsson',
      email: null,
      status: 'invited',
    })
    const fetchMock = mockApi({
      ...base(),
      [listKey()]: () => [200, page([ALICE])],
      'POST /api/admin/users': () => [201, created],
      'POST /api/admin/users/9/invite': () => [
        200,
        {
          delivery: 'link',
          sent_to: null,
          link: 'http://localhost:5173/activate/abc',
          expires_at: '2026-10-15T12:00:00Z',
        },
      ],
    })
    const router = renderApp('/admin/users')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('link', { name: 'Add user' }))
    expect(router.state.location.pathname).toBe('/admin/users/new')
    // Only roles this admin may assign are offered.
    const role = screen.getByRole('combobox', { name: 'Role' })
    await waitFor(() => {
      expect(
        within(role)
          .getAllByRole('option')
          .map((o) => o.textContent),
      ).toEqual([
        'Choose a role',
        'General manager',
        'Front office manager',
        'Receptionist',
        'Housekeeping supervisor',
        'Room attendant',
      ])
    })
    await user.type(screen.getByLabelText('Full name'), 'Carl Carlsson')
    await user.type(screen.getByLabelText('Username'), 'carl')
    await user.type(screen.getByLabelText('Job title (optional)'), 'Porter')
    await user.selectOptions(role, 'Room attendant')
    await user.click(screen.getByRole('button', { name: 'Create user' }))

    const heading = await screen.findByRole('heading', { name: 'Carl Carlsson (carl) was created' })
    expect(heading).toHaveFocus()
    expect(bodyOf(fetchMock, '/api/admin/users')).toEqual({
      full_name: 'Carl Carlsson',
      username: 'carl',
      email: null,
      phone: null,
      job_title: 'Porter',
      employee_id: null,
      expires_at: null,
      role_id: 6,
    })

    await user.click(screen.getByRole('button', { name: 'Show activation link' }))
    expect(await screen.findByLabelText('Activation link')).toHaveValue(
      'http://localhost:5173/activate/abc',
    )
    await user.click(screen.getByRole('button', { name: 'Copy' }))
    expect(await screen.findByText(/Link copied/)).toBeInTheDocument()
  })

  it('offers "Send invite" when the new user has an email', async () => {
    mockApi({
      ...base(),
      'POST /api/admin/users': () => [201, detail({ id: 9, status: 'invited' })],
      'POST /api/admin/users/9/invite': () => [
        200,
        {
          delivery: 'email',
          sent_to: 'a***@hotel.example',
          link: null,
          expires_at: '2026-10-15T12:00:00Z',
        },
      ],
    })
    renderApp('/admin/users/new')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Full name'), 'Alice Andersson')
    await user.type(screen.getByLabelText('Username'), 'alice')
    await user.type(screen.getByLabelText('Email (optional)'), 'alice@hotel.example')
    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'Receptionist' })).toBeInTheDocument()
    })
    await user.selectOptions(screen.getByRole('combobox', { name: 'Role' }), 'Receptionist')
    await user.click(screen.getByRole('button', { name: 'Create user' }))

    await user.click(await screen.findByRole('button', { name: 'Send invite' }))

    expect(await screen.findByText(/emailed to a\*\*\*@hotel.example/)).toBeInTheDocument()
  })

  it('validates before sending and focuses the first problem', async () => {
    const fetchMock = mockApi({ ...base() })
    renderApp('/admin/users/new')
    const user = userEvent.setup()

    await user.type(await screen.findByLabelText('Username'), 'a b')
    await user.type(screen.getByLabelText('Email (optional)'), 'nope')
    await user.click(screen.getByRole('button', { name: 'Create user' }))

    expect(screen.getByLabelText('Full name')).toHaveFocus()
    expect(screen.getByLabelText('Full name')).toHaveAccessibleDescription('Enter the full name.')
    expect(screen.getByLabelText('Username')).toHaveAccessibleDescription(/^Use 3–50 letters/)
    expect(screen.getByLabelText('Email (optional)')).toHaveAccessibleDescription(
      /^Enter a valid email address/,
    )
    expect(screen.getByRole('combobox', { name: 'Role' })).toHaveAccessibleDescription(
      'Choose a role.',
    )
    expect(requested(fetchMock, 'POST')).toEqual([])
  })

  it('puts a taken username on the username field', async () => {
    mockApi({
      ...base(),
      'POST /api/admin/users': () => [
        409,
        { detail: "The username 'alice' is already taken.", code: 'username_taken' },
      ],
    })
    renderApp('/admin/users/new')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Full name'), 'Alice Again')
    await user.type(screen.getByLabelText('Username'), 'alice')
    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'Receptionist' })).toBeInTheDocument()
    })
    await user.selectOptions(screen.getByRole('combobox', { name: 'Role' }), 'Receptionist')
    await user.click(screen.getByRole('button', { name: 'Create user' }))

    await waitFor(() => {
      expect(screen.getByLabelText('Username')).toHaveFocus()
    })
    expect(screen.getByLabelText('Username')).toHaveAccessibleDescription(
      /^The username 'alice' is already taken\./,
    )
  })

  it('is only for users with user.manage', async () => {
    mockApi({ ...base(FRONT_OFFICE_MANAGER_USER) })
    renderApp('/admin/users/new')

    expect(
      await screen.findByRole('heading', { name: 'You do not have access' }),
    ).toBeInTheDocument()
  })
})

// --- detail: role ---------------------------------------------------------------------------

function detailApi(user: UserDetail, me = MANAGER_USER, extra = {}) {
  return mockApi({
    ...base(me),
    'GET /api/admin/users/7': () => [200, user],
    [AUDIT_KEY]: () => [200, NO_AUDIT],
    ...extra,
  })
}

describe('user detail: role', () => {
  it('changes the role after confirming, then returns focus', async () => {
    const fetchMock = detailApi(detail(), MANAGER_USER, {
      'PUT /api/admin/users/7/role': () => [
        200,
        detail({
          roles: [
            {
              property: { id: 1, name: 'Grand Hotel Stockholm', time_zone: 'Europe/Stockholm' },
              role: { id: 3, name: 'Front office manager' },
            },
          ],
        }),
      ],
    })
    renderApp('/admin/users/7')
    const user = userEvent.setup()
    const select = await screen.findByRole('combobox', { name: 'Role at this property' })
    await waitFor(() => {
      expect(select).toHaveValue('4')
    })
    // Admin is listed nowhere: the GM may not assign it.
    expect(within(select).queryByRole('option', { name: 'Admin' })).not.toBeInTheDocument()

    await user.selectOptions(select, 'Front office manager')
    const change = screen.getByRole('button', { name: 'Change role' })
    await user.click(change)

    const dialog = screen.getByRole('dialog', { name: "Change Alice Andersson's role?" })
    expect(dialog).toHaveAccessibleDescription(
      'Alice Andersson (alice) will get the role Front office manager and be signed out of all sessions.',
    )
    await user.click(within(dialog).getByRole('button', { name: 'Change role' }))

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    expect(bodyOf(fetchMock, '/api/admin/users/7/role')).toEqual({ role_id: 3 })
    expect(
      screen.getByText('Alice Andersson now has the role Front office manager.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Change role' })).toHaveFocus()
  })

  it('explains why the role cannot be changed', async () => {
    detailApi(
      detail({ restrictions: { change_role: 'You cannot change your own role.' } }),
      MANAGER_USER,
    )
    renderApp('/admin/users/7')
    const user = userEvent.setup()

    const change = await screen.findByRole('button', { name: 'Change role' })
    expect(change).toHaveAttribute('aria-disabled', 'true')
    expect(change).toHaveAccessibleDescription('You cannot change your own role.')
    expect(screen.getByRole('combobox', { name: 'Role at this property' })).toBeDisabled()
    await user.click(change)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('is read only without user.manage, with no actions or history', async () => {
    detailApi(detail(), FRONT_OFFICE_MANAGER_USER)
    renderApp('/admin/users/7')

    expect(await screen.findByRole('heading', { name: 'Alice Andersson' })).toBeInTheDocument()
    expect(screen.getByText('Receptionist', { selector: 'p' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Actions' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Recent changes' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit profile' })).not.toBeInTheDocument()
  })
})

// --- detail: dialogs ------------------------------------------------------------------------

describe('user detail: confirmation dialogs', () => {
  it('Escape cancels, focus returns to the trigger, nothing is sent', async () => {
    const fetchMock = detailApi(detail())
    renderApp('/admin/users/7')
    const user = userEvent.setup()
    const deactivate = await screen.findByRole('button', { name: 'Deactivate' })

    await user.click(deactivate)
    const dialog = screen.getByRole('dialog', { name: 'Deactivate Alice Andersson?' })
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus()

    await user.keyboard('{Escape}')

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(deactivate).toHaveFocus()
    expect(requested(fetchMock, 'POST')).toEqual([])
  })

  it('keeps Tab inside the dialog', async () => {
    detailApi(detail())
    renderApp('/admin/users/7')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Sign out everywhere' }))
    const dialog = screen.getByRole('dialog')
    const cancel = within(dialog).getByRole('button', { name: 'Cancel' })
    const confirm = within(dialog).getByRole('button', { name: 'Sign out everywhere' })

    await user.tab()
    expect(confirm).toHaveFocus()
    await user.tab()
    expect(cancel).toHaveFocus()
    await user.tab({ shift: true })
    expect(confirm).toHaveFocus()
  })

  it('deactivates after confirming; the same button becomes Reactivate and keeps focus', async () => {
    detailApi(detail(), MANAGER_USER, {
      'POST /api/admin/users/7/deactivate': () => [
        200,
        detail({
          status: 'deactivated',
          deactivated_at: '2026-10-12T13:00:00Z',
          restrictions: { deactivate: 'alice is already deactivated.' },
        }),
      ],
    })
    renderApp('/admin/users/7')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Deactivate' }))
    expect(screen.getByRole('dialog')).toHaveAccessibleDescription(
      'Alice Andersson (alice) is signed out and can no longer sign in. You can reactivate them later.',
    )
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Deactivate' }))

    expect(await screen.findByText('Alice Andersson was deactivated.')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reactivate' })).toHaveFocus()
    expect(screen.getAllByText('Deactivated').length).toBeGreaterThan(0)
  })

  it('reports how many sessions were ended', async () => {
    detailApi(detail(), MANAGER_USER, {
      'POST /api/admin/users/7/revoke-sessions': () => [200, { revoked_sessions: 2 }],
    })
    renderApp('/admin/users/7')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Sign out everywhere' }))
    expect(
      screen.getByRole('dialog', { name: 'Sign Alice Andersson out everywhere?' }),
    ).toBeVisible()
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Sign out everywhere' }),
    )

    expect(
      await screen.findByText('Alice Andersson was signed out of 2 sessions.'),
    ).toBeInTheDocument()
  })

  it('shows a reset link in the dialog when it cannot be emailed', async () => {
    detailApi(detail({ email: null }), MANAGER_USER, {
      'POST /api/admin/users/7/reset-password': () => [
        200,
        {
          delivery: 'link',
          sent_to: null,
          link: 'http://localhost:5173/reset-password/xyz',
          expires_at: '2026-10-12T14:00:00Z',
        },
      ],
    })
    renderApp('/admin/users/7')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Reset password' }))
    expect(screen.getByRole('dialog')).toHaveAccessibleDescription(
      /You will get a reset link to give them in person/,
    )
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Send reset link' }),
    )

    const dialog = await screen.findByRole('dialog', { name: 'Reset link ready' })
    expect(within(dialog).getByLabelText('Activation link')).toHaveValue(
      'http://localhost:5173/reset-password/xyz',
    )
    await user.click(within(dialog).getByRole('button', { name: 'Done' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('keeps the dialog open with the error when the server refuses', async () => {
    detailApi(detail(), MANAGER_USER, {
      'POST /api/admin/users/7/deactivate': () => [
        409,
        { detail: 'alice is the last active Admin of this property.', code: 'last_admin' },
      ],
    })
    renderApp('/admin/users/7')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Deactivate' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Deactivate' }))

    const dialog = screen.getByRole('dialog')
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'alice is the last active Admin of this property.',
    )
  })

  it('disables forbidden actions with the reason', async () => {
    detailApi(
      detail({
        restrictions: {
          deactivate: 'You cannot deactivate yourself.',
          send_invite: 'alice has already activated their account.',
          reactivate: 'alice is not deactivated.',
        },
      }),
    )
    renderApp('/admin/users/7')

    const deactivate = await screen.findByRole('button', { name: 'Deactivate' })
    expect(deactivate).toHaveAttribute('aria-disabled', 'true')
    expect(deactivate).toHaveAccessibleDescription('You cannot deactivate yourself.')
    expect(screen.getByRole('button', { name: 'Reset password' })).not.toHaveAttribute(
      'aria-disabled',
    )
  })
})

// --- detail: profile and history -----------------------------------------------------------

describe('user detail: profile and history', () => {
  it('edits the profile inline, sending only what changed', async () => {
    const fetchMock = detailApi(detail(), MANAGER_USER, {
      'PATCH /api/admin/users/7': () => [200, detail({ phone: '+46 70 123 45 67' })],
    })
    renderApp('/admin/users/7')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Edit profile' }))
    expect(screen.getByLabelText('Full name')).toHaveFocus()
    await user.type(screen.getByLabelText('Phone (optional)'), '+46 70 123 45 67')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Edit profile' })).toHaveFocus()
    })
    expect(screen.getByText('+46 70 123 45 67')).toBeInTheDocument()
    expect(bodyOf(fetchMock, '/api/admin/users/7')).toEqual({ phone: '+46 70 123 45 67' })
  })

  it('lists recent changes with who made them', async () => {
    detailApi(detail(), ADMIN_USER, {
      [AUDIT_KEY]: () => [
        200,
        {
          items: [
            {
              id: 'change-2',
              source: 'change',
              occurred_at: '2026-10-12T10:00:00Z',
              action: 'user.role_changed',
              actor: { id: 5, username: 'admin', full_name: 'Ada Admin' },
              target_type: 'user',
              target_id: '7',
              target_label: 'Alice Andersson',
              before: { role_id: 6, role_name: 'Room attendant' },
              after: { role_id: 4, role_name: 'Receptionist', sessions_revoked: 0 },
              details: null,
              ip: '10.0.0.5',
            },
            {
              id: 'auth-9',
              source: 'auth',
              occurred_at: '2026-10-11T09:00:00Z',
              action: 'auth.login_failed',
              actor: { id: 7, username: 'alice', full_name: 'Alice Andersson' },
              target_type: 'user',
              target_id: '7',
              target_label: 'Alice Andersson',
              before: null,
              after: null,
              details: { reason: 'bad_password' },
              ip: '10.0.0.9',
            },
            {
              id: 'change-1',
              source: 'change',
              occurred_at: '2026-10-11T08:00:00Z',
              action: 'user.created',
              actor: null,
              target_type: 'user',
              target_id: '7',
              target_label: 'Alice Andersson',
              before: null,
              after: { username: 'alice' },
              details: null,
              ip: null,
            },
          ],
          next_cursor: null,
        },
      ],
    })
    renderApp('/admin/users/7')

    const history = await screen.findByRole('region', { name: 'Recent changes' })
    const list = await within(history).findByRole('list')
    const items = within(list).getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('Changed the role from Room attendant to Receptionist')
    expect(items[0]).toHaveTextContent('by Ada Admin')
    expect(items[1]).toHaveTextContent('Sign-in failed (bad password)')
    expect(items[1]).toHaveTextContent('by Alice Andersson')
    expect(items[2]).toHaveTextContent('Created the account')
    expect(items[2]).toHaveTextContent('by the system')
  })

  it('handles an unknown user', async () => {
    mockApi({ ...base(), 'GET /api/admin/users/404': () => [404, { detail: 'not found' }] })
    renderApp('/admin/users/404')

    expect(await screen.findByRole('heading', { name: 'User not found' })).toBeInTheDocument()
  })
})
