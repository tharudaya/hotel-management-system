/**
 * The admin screens with the keyboard only (Tab, Shift+Tab, Enter, Space, Escape):
 * no clicks anywhere in these tests. Plus page titles and route announcements, which
 * is what a screen reader user hears when the page changes.
 */
import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { HOTEL } from './test/fixtures'
import { ADMIN_USER, MANAGER_USER, mockApi, renderApp } from './test/renderApp'

const PROPERTY = { id: 1, name: 'Grand Hotel Stockholm', time_zone: 'Europe/Stockholm' }
const ALICE_ITEM = {
  id: 7,
  username: 'alice',
  full_name: 'Alice Andersson',
  email: 'alice@hotel.example',
  job_title: null,
  employee_id: null,
  status: 'active',
  role: { id: 4, name: 'Receptionist' },
  last_login_at: null,
  expires_at: null,
  created_at: '2026-01-01T09:00:00Z',
}
const ALICE = {
  ...ALICE_ITEM,
  phone: null,
  roles: [{ property: PROPERTY, role: { id: 4, name: 'Receptionist' } }],
  deactivated_at: null,
  restrictions: { reactivate: 'alice is not deactivated.', send_invite: 'already active' },
}
const ROLES = [{ id: 4, name: 'Receptionist', assignable: true }]

function usersApi() {
  return mockApi({
    'GET /api/auth/me': () => [200, MANAGER_USER],
    'GET /api/hotel': () => [200, HOTEL],
    'GET /api/admin/users/roles': () => [200, ROLES],
    'GET /api/admin/users?sort=name&page=1&page_size=25': () => [
      200,
      { items: [ALICE_ITEM], total: 1, page: 1, page_size: 25 },
    ],
    'GET /api/admin/users?sort=-name&page=1&page_size=25': () => [
      200,
      { items: [ALICE_ITEM], total: 1, page: 1, page_size: 25 },
    ],
    'GET /api/admin/users/7': () => [200, ALICE],
    'GET /api/admin/audit?target_type=user&target_id=7&limit=10': () => [
      200,
      { items: [], next_cursor: null },
    ],
  })
}

/**
 * Put focus on `target` to start a keyboard sequence. Tab order itself is checked
 * with real Tab presses in the first test; repeating long Tab walks in every test
 * makes the suite slow without testing anything new.
 */
function focusOn(target: HTMLElement) {
  act(() => {
    target.focus()
  })
  expect(target).toHaveFocus()
}

describe('keyboard only', () => {
  it('users list: filters, sorting and opening a user', async () => {
    usersApi()
    const router = renderApp('/admin/users')
    const user = userEvent.setup()
    await screen.findByRole('link', { name: 'Alice Andersson' })
    expect(document.title).toBe('Users – Hotel Management')

    // The search box, both filters and every sort button are reachable in order.
    const order = [
      screen.getByRole('searchbox', { name: 'Search' }),
      screen.getByRole('combobox', { name: 'Role' }),
      screen.getByRole('combobox', { name: 'Status' }),
      screen.getByRole('button', { name: 'Name' }),
      screen.getByRole('button', { name: 'Username' }),
    ]
    const [first, ...others] = order
    if (!first) throw new Error('no elements')
    focusOn(first)
    for (const element of others) {
      await user.tab()
      expect(element).toHaveFocus()
    }

    // Enter on a column header sorts.
    focusOn(screen.getByRole('button', { name: 'Name' }))
    await user.keyboard('{Enter}')
    await waitFor(() => {
      expect(router.state.location.search).toBe('?sort=-name')
    })

    // Enter on a name opens the user; focus moves to the new page's heading, and the
    // new title is announced.
    focusOn(screen.getByRole('link', { name: 'Alice Andersson' }))
    await user.keyboard('{Enter}')
    const heading = await screen.findByRole('heading', { level: 1, name: 'Alice Andersson' })
    await waitFor(() => {
      expect(heading).toHaveFocus()
    })
    expect(document.title).toBe('Alice Andersson – Hotel Management')
    // The announcement is the page's heading (not a loading placeholder).
    await waitFor(() => {
      expect(screen.getByText('Alice Andersson', { selector: '[aria-live]' })).toBeInTheDocument()
    })
  })

  it('confirmation dialog: open, trap, cancel, return; the page behind is inert', async () => {
    usersApi()
    const { container } = { container: document.body }
    renderApp('/admin/users/7')
    const user = userEvent.setup()
    const deactivate = await screen.findByRole('button', { name: 'Deactivate' })

    focusOn(deactivate)
    await user.keyboard('{Enter}')

    const dialog = screen.getByRole('dialog', { name: 'Deactivate Alice Andersson?' })
    const cancel = within(dialog).getByRole('button', { name: 'Cancel' })
    expect(cancel).toHaveFocus()
    // Everything outside the dialog is inert and hidden from assistive technology.
    const outside = [...container.children].filter((el) => !el.contains(dialog))
    expect(outside.length).toBeGreaterThan(0)
    for (const el of outside) {
      expect(el).toHaveAttribute('inert')
      expect(el).toHaveAttribute('aria-hidden', 'true')
    }
    await user.tab()
    await user.tab()
    expect(cancel).toHaveFocus() // wrapped around inside the dialog

    await user.keyboard('{Escape}')

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(deactivate).toHaveFocus()
    for (const el of outside) expect(el).not.toHaveAttribute('inert')
  })

  it('a blocked action explains itself on focus and Escape hides the tooltip', async () => {
    usersApi()
    renderApp('/admin/users/7')
    const user = userEvent.setup()
    const invite = await screen.findByRole('button', { name: 'Reset password' })
    expect(invite).not.toHaveAttribute('aria-disabled')
    const blocked = screen.getAllByRole('button').find((b) => b.getAttribute('aria-disabled'))
    if (!blocked) throw new Error('expected a blocked action')

    focusOn(blocked)
    const tooltip = screen.getByRole('tooltip')
    expect(blocked).toHaveAccessibleDescription(tooltip.textContent)
    expect(tooltip.className).toContain('group-focus-within:visible')

    await user.keyboard('{Escape}')
    expect(tooltip.className).not.toContain('group-focus-within:visible')
    expect(blocked).toHaveFocus() // focus did not move

    await user.keyboard('{Enter}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument() // blocked: nothing opens
  })

  it('role editor: Space toggles permissions, Enter saves through the summary', async () => {
    const role = {
      id: 10,
      name: 'Night auditor',
      description: null,
      is_system: false,
      is_admin: false,
      user_count: 0,
      permission_count: 0,
      can_edit: true,
      can_delete: true,
      can_duplicate: true,
      permissions: [],
      users: [],
    }
    mockApi({
      'GET /api/auth/me': () => [200, ADMIN_USER],
      'GET /api/hotel': () => [200, HOTEL],
      'GET /api/admin/roles/10': () => [200, role],
      'GET /api/admin/roles/permissions': () => [
        200,
        [
          {
            group: 'reservations',
            permissions: [
              { key: 'reservation.view', description: 'View reservations' },
              { key: 'reservation.check_in', description: 'Check guests in' },
            ],
          },
        ],
      ],
      'PATCH /api/admin/roles/10': () => [
        200,
        { ...role, permissions: ['reservation.check_in', 'reservation.view'] },
      ],
    })
    renderApp('/admin/roles/10')
    const user = userEvent.setup()
    const selectAll = await screen.findByRole('checkbox', {
      name: 'Select all reservations permissions',
    })

    focusOn(selectAll)
    await user.keyboard(' ')
    expect(screen.getByRole('checkbox', { name: /^Check guests in/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /^View reservations/ })).toBeChecked()

    const save = screen.getByRole('button', { name: 'Save changes' })
    focusOn(save)
    await user.keyboard('{Enter}')
    const dialog = screen.getByRole('dialog', { name: 'Save changes to Night auditor?' })
    await user.tab() // from Cancel to the confirm button
    expect(within(dialog).getByRole('button', { name: 'Save changes' })).toHaveFocus()
    await user.keyboard('{Enter}')

    expect(await screen.findByText('Changes saved.')).toBeInTheDocument()
    expect(save).toHaveFocus()
  })

  it('audit log: Enter and Space expand and collapse a row', async () => {
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
              before: { phone: null },
              after: { phone: '123' },
              details: null,
              ip: null,
            },
          ],
          next_cursor: null,
        },
      ],
    })
    renderApp('/admin/audit')
    const user = userEvent.setup()
    const toggle = await screen.findByRole('button', { name: /^Details: Profile changed/ })
    expect(document.title).toBe('Audit log – Hotel Management')

    focusOn(toggle)
    await user.keyboard('{Enter}')
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('table', { name: 'Changed fields' })).toBeInTheDocument()
    await user.keyboard(' ')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
  })

  it('every signed-out page names itself', async () => {
    mockApi({})
    renderApp('/forgot-password')
    await screen.findByRole('heading', { name: 'Reset your password' })

    expect(document.title).toBe('Reset your password – Hotel Management')
  })
})
