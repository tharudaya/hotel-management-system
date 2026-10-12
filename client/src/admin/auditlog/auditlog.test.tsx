import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { AuditEntry, AuditPage } from '../../api/types'
import { HOTEL } from '../../test/fixtures'
import { MANAGER_USER, mockApi, renderApp } from '../../test/renderApp'
import { type AuditFilters, NO_FILTERS, toApiQuery } from './params'

function entry(overrides: Partial<AuditEntry>): AuditEntry {
  return {
    id: 'change-1',
    source: 'change',
    occurred_at: '2026-10-12T08:30:00Z',
    action: 'user.updated',
    actor: { id: 2, username: 'manager', full_name: 'Morgan' },
    target_type: 'user',
    target_id: '7',
    target_label: 'Alice Andersson',
    before: { job_title: null, expires_at: null },
    after: { job_title: 'Night audit', expires_at: '2026-12-31T22:59:59Z' },
    details: null,
    ip: '10.0.0.5',
    ...overrides,
  }
}

const PROFILE_CHANGE = entry({})
const ROLE_CHANGE = entry({
  id: 'change-2',
  occurred_at: '2026-10-12T08:00:00Z',
  action: 'role.updated',
  target_type: 'role',
  target_id: '10',
  target_label: 'Night auditor',
  before: null,
  after: { permissions: { added: ['report.view'], removed: ['guest.view'] } },
})
const DELETED_ROLE = entry({
  id: 'change-3',
  occurred_at: '2026-10-12T07:00:00Z',
  action: 'role.deleted',
  target_type: 'role',
  target_id: '11',
  target_label: 'Temp role',
  before: { name: 'Temp role' },
  after: null,
})
const FAILED_SIGN_IN = entry({
  id: 'auth-5',
  source: 'auth',
  occurred_at: '2026-10-11T22:15:00Z',
  action: 'auth.login_failed',
  actor: { id: null, username: 'mallory', full_name: null },
  target_type: null,
  target_id: null,
  target_label: null,
  before: null,
  after: null,
  details: { reason: 'unknown_user' },
  ip: '203.0.113.9',
})
const SYSTEM_CHANGE = entry({
  id: 'change-0',
  occurred_at: '2026-10-01T00:00:00Z',
  action: 'user.created',
  actor: null,
  before: null,
  after: { username: 'alice' },
})

const ALL = [PROFILE_CHANGE, ROLE_CHANGE, DELETED_ROLE, FAILED_SIGN_IN, SYSTEM_CHANGE]

const key = (filters: Partial<AuditFilters> = {}, cursor?: string) =>
  `GET /api/admin/audit?${toApiQuery({ ...NO_FILTERS, ...filters })}${cursor ? `&cursor=${cursor}` : ''}`

function page(items: AuditEntry[], next: string | null = null): AuditPage {
  return { items, next_cursor: next }
}

function api(handlers: Record<string, () => [number, unknown]>) {
  return mockApi({
    'GET /api/auth/me': () => [200, MANAGER_USER],
    'GET /api/hotel': () => [200, HOTEL],
    ...handlers,
  })
}

function auditRequests(fetchMock: ReturnType<typeof mockApi>): string[] {
  return fetchMock.mock.calls
    .map(([path]) => (typeof path === 'string' ? path : ''))
    .filter((path) => path.startsWith('/api/admin/audit'))
}

const row = (name: RegExp) => screen.getByRole('row', { name })

describe('audit log table', () => {
  it('shows time in hotel time, actor, action and target', async () => {
    api({ [key()]: () => [200, page(ALL)] })
    renderApp('/admin/audit')

    const table = await screen.findByRole('table', { name: 'Audit log, newest first' })
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((th) => th.textContent),
    ).toEqual(['Details', 'Time', 'Actor', 'Action', 'Target'])
    const profile = row(/Profile changed/)
    expect(profile).toHaveTextContent('12 Oct 2026, 10:30') // UTC+2
    expect(profile).toHaveTextContent('Morgan')
    expect(within(profile).getByRole('link', { name: 'Alice Andersson' })).toHaveAttribute(
      'href',
      '/admin/users/7',
    )
    expect(
      within(row(/Role changed/)).getByRole('link', { name: 'Night auditor' }),
    ).toHaveAttribute('href', '/admin/roles/10')
    // A deleted role keeps its name but has no page.
    expect(within(row(/Role deleted/)).queryByRole('link')).not.toBeInTheDocument()
    expect(row(/Role deleted/)).toHaveTextContent('Temp role')
    const failed = row(/Sign-in failed/)
    expect(failed).toHaveTextContent('mallory (no such user)')
    expect(failed).toHaveTextContent('12 Oct 2026, 00:15') // past midnight, hotel time
    expect(row(/User created/)).toHaveTextContent('System')
    expect(screen.getByRole('status', { name: '' })).toHaveTextContent('Showing 5 entries')
  })

  it('expands a row into a readable before and after', async () => {
    api({ [key()]: () => [200, page(ALL)] })
    renderApp('/admin/audit')
    const user = userEvent.setup()

    const toggle = await screen.findByRole('button', {
      name: 'Details: Profile changed, 12 Oct 2026, 10:30',
    })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')

    const details = document.getElementById(toggle.getAttribute('aria-controls') ?? '')
    if (!details) throw new Error('the toggle controls no details row')
    const diff = within(details).getByRole('table', { name: 'Changed fields' })
    const jobTitle = within(diff).getByRole('row', { name: /Job title/ })
    expect(
      within(jobTitle)
        .getAllByRole('cell')
        .map((c) => c.textContent),
    ).toEqual(['—', 'Night audit'])
    const expiry = within(diff).getByRole('row', { name: /Access expires/ })
    expect(expiry).toHaveTextContent('31 Dec 2026, 23:59') // shown in hotel time
    expect(details).toHaveTextContent('user.updated · from 10.0.0.5')

    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('table', { name: 'Changed fields' })).not.toBeInTheDocument()
  })

  it('shows permission changes and sign-in details', async () => {
    api({ [key()]: () => [200, page(ALL)] })
    renderApp('/admin/audit')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: /^Details: Role changed/ }))
    await user.click(screen.getByRole('button', { name: /^Details: Sign-in failed/ }))

    expect(screen.getByText('Permissions added').nextElementSibling).toHaveTextContent(
      'report.view',
    )
    expect(screen.getByText('Permissions removed').nextElementSibling).toHaveTextContent(
      'guest.view',
    )
    expect(screen.getByText('Reason').nextElementSibling).toHaveTextContent('unknown user')
    expect(screen.getByText(/from 203\.0\.113\.9/)).toBeInTheDocument()
  })
})

describe('audit log filters', () => {
  it('reads every filter from the URL and sends it to the API', async () => {
    const filters: Partial<AuditFilters> = {
      actor: 'manager',
      action: 'user.updated',
      targetType: 'user',
      targetId: '7',
      from: '2026-10-01',
      to: '2026-10-12',
    }
    const fetchMock = api({ [key(filters)]: () => [200, page([PROFILE_CHANGE])] })
    renderApp(
      '/admin/audit?actor=manager&action=user.updated&target_type=user&target_id=7&from=2026-10-01&to=2026-10-12',
    )

    await screen.findByRole('row', { name: /Profile changed/ })
    expect(screen.getByLabelText('Actor username')).toHaveValue('manager')
    expect(screen.getByRole('combobox', { name: 'Action' })).toHaveValue('user.updated')
    expect(screen.getByRole('combobox', { name: 'Target type' })).toHaveValue('user')
    expect(screen.getByLabelText('Target ID')).toHaveValue('7')
    expect(screen.getByLabelText('From')).toHaveValue('2026-10-01')
    expect(screen.getByLabelText('To')).toHaveValue('2026-10-12')
    expect(auditRequests(fetchMock)).toEqual([
      '/api/admin/audit?limit=50&actor=manager&action=user.updated&target_type=user&target_id=7&date_from=2026-10-01&date_to=2026-10-12',
    ])
  })

  it('writes filter changes to the URL and refetches', async () => {
    const fetchMock = api({
      [key()]: () => [200, page(ALL)],
      [key({ action: 'auth.login_failed' })]: () => [200, page([FAILED_SIGN_IN])],
      [key({ action: 'auth.login_failed', actor: 'mallory' })]: () => [200, page([FAILED_SIGN_IN])],
    })
    const router = renderApp('/admin/audit')
    const user = userEvent.setup()
    await screen.findByRole('table')

    await user.selectOptions(screen.getByRole('combobox', { name: 'Action' }), 'Sign-in failed')
    await waitFor(() => {
      expect(screen.queryByRole('row', { name: /Profile changed/ })).not.toBeInTheDocument()
    })
    expect(router.state.location.search).toBe('?action=auth.login_failed')

    // Pasted in one go: typing key by key could pause past the debounce on a busy
    // machine and (correctly) search for a partial name.
    await user.click(screen.getByLabelText('Actor username'))
    await user.paste('mallory')
    await waitFor(() => {
      expect(router.state.location.search).toBe('?actor=mallory&action=auth.login_failed')
    })
    // Debounced: one request once typing pauses.
    expect(auditRequests(fetchMock).filter((p) => p.includes('actor='))).toHaveLength(1)
  })

  it('refuses a date range that ends before it starts, without asking the server', async () => {
    const fetchMock = api({})
    renderApp('/admin/audit?from=2026-10-12&to=2026-10-01')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The start date is after the end date.',
    )
    expect(screen.getByLabelText('From')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('To')).toHaveAccessibleDescription(
      'The start date is after the end date.',
    )
    expect(auditRequests(fetchMock)).toEqual([])
  })

  it('shows an empty state that clears the filters', async () => {
    api({
      [key({ targetType: 'reservation' })]: () => [200, page([])],
      [key()]: () => [200, page(ALL)],
    })
    const router = renderApp('/admin/audit?target_type=reservation')

    expect(await screen.findByText('No entries match these filters.')).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Clear filters' }))

    expect(await screen.findByRole('row', { name: /Profile changed/ })).toBeInTheDocument()
    expect(router.state.location.search).toBe('')
  })

  it('ignores unknown values in the URL', async () => {
    const fetchMock = api({ [key()]: () => [200, page(ALL)] })
    renderApp('/admin/audit?action=user.exploded&target_type=guest&from=yesterday')

    await screen.findByRole('table')
    expect(auditRequests(fetchMock)).toEqual(['/api/admin/audit?limit=50'])
  })
})

describe('audit log pagination', () => {
  it('loads older entries with the cursor until there are none', async () => {
    const older = entry({
      id: 'change-9',
      occurred_at: '2026-09-30T10:00:00Z',
      action: 'user.deactivated',
    })
    const fetchMock = api({
      [key()]: () => [200, page([PROFILE_CHANGE, ROLE_CHANGE], 'CURSOR1')],
      [key({}, 'CURSOR1')]: () => [200, page([older])],
    })
    renderApp('/admin/audit')
    const user = userEvent.setup()

    await screen.findByRole('row', { name: /Profile changed/ })
    expect(screen.getByText(/Showing 2 entries \(older entries available\)/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Load older entries' }))

    expect(await screen.findByRole('row', { name: /User deactivated/ })).toBeInTheDocument()
    // Appended, not replaced.
    expect(screen.getByRole('row', { name: /Profile changed/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load older entries' })).not.toBeInTheDocument()
    expect(screen.getByText('Showing 3 entries')).toBeInTheDocument()
    expect(auditRequests(fetchMock)).toEqual([
      '/api/admin/audit?limit=50',
      '/api/admin/audit?limit=50&cursor=CURSOR1',
    ])
  })

  it('shows an error with a retry', async () => {
    let calls = 0
    api({ [key()]: () => (++calls <= 3 ? [500, { detail: 'boom' }] : [200, page(ALL)]) })
    renderApp('/admin/audit')

    const alert = await screen.findByRole('alert', {}, { timeout: 5000 })
    expect(alert).toHaveTextContent('The audit log could not be loaded.')
    await userEvent.setup().click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('row', { name: /Profile changed/ })).toBeInTheDocument()
  }, 10_000)
})
