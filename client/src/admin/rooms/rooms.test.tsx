import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { RoomOut, RoomTypeOut, User } from '../../api/types'
import { HOTEL } from '../../test/fixtures'
import { ADMIN_USER, MANAGER_USER, mockApi, renderApp } from '../../test/renderApp'

// --- fixtures -----------------------------------------------------------------------------

function roomType(overrides: Partial<RoomTypeOut>): RoomTypeOut {
  return {
    id: 1,
    code: 'STD',
    name: 'Standard Queen',
    max_occupancy: 2,
    bed_setup: '1 queen',
    description: null,
    is_active: true,
    active_room_count: 2,
    upcoming_reservations: 0,
    retire_blocked:
      'Room type STD still has 2 active rooms. Retire them or give them another type first.',
    created_at: '2026-01-01T09:00:00Z',
    updated_at: '2026-01-01T09:00:00Z',
    ...overrides,
  }
}

const STD = roomType({})
const DLX = roomType({
  id: 2,
  code: 'DLX',
  name: 'Deluxe King',
  max_occupancy: 3,
  bed_setup: '1 king',
  active_room_count: 0,
  retire_blocked: null,
})
const OLD = roomType({
  id: 3,
  code: 'OLD',
  name: 'Old wing',
  is_active: false,
  active_room_count: 0,
  retire_blocked: null,
})

function room(overrides: Partial<RoomOut>): RoomOut {
  return {
    id: 101,
    number: '101',
    floor: 1,
    room_type: { id: 1, code: 'STD', name: 'Standard Queen', is_active: true },
    notes: null,
    housekeeping_status: 'inspected',
    is_active: true,
    retired_at: null,
    retired_by: null,
    guest_in_house: false,
    upcoming_reservations: 0,
    retire_blocked: null,
    type_change_blocked: null,
    version: 3,
    created_at: '2026-01-01T09:00:00Z',
    updated_at: '2026-01-01T09:00:00Z',
    ...overrides,
  }
}

const R101 = room({})
const R102 = room({
  id: 102,
  number: '102',
  notes: 'Connecting door to 103',
  guest_in_house: true,
  retire_blocked: 'Room 102 has a guest checked in.',
  type_change_blocked: 'Room 102 has a guest checked in.',
})
const R201 = room({ id: 201, number: '201', floor: 2 })
const ACTIVE_ROOMS = [R101, R102, R201]

type Handlers = Record<string, () => [number, unknown?]>

function api(me: User = ADMIN_USER, extra: Handlers = {}) {
  return mockApi({
    'GET /api/auth/me': () => [200, me],
    'GET /api/hotel': () => [200, HOTEL],
    'GET /api/admin/rooms?status=active': () => [200, ACTIVE_ROOMS],
    'GET /api/admin/rooms?status=all': () => [200, ACTIVE_ROOMS],
    'GET /api/admin/room-types?include_retired=true': () => [200, [DLX, STD, OLD]],
    'GET /api/admin/room-types': () => [200, [DLX, STD]],
    ...extra,
  })
}

function bodyOf(fetchMock: ReturnType<typeof mockApi>, method: string, path: string): unknown {
  const call = fetchMock.mock.calls.find(([p, init]) => p === path && init?.method === method)
  const body = call?.[1]?.body
  return typeof body === 'string' ? JSON.parse(body) : undefined
}

function calls(fetchMock: ReturnType<typeof mockApi>, key: string): number {
  return fetchMock.mock.calls.filter(
    ([p, init]) => `${init?.method ?? 'GET'} ${typeof p === 'string' ? p : ''}` === key,
  ).length
}

// --- permission guard ---------------------------------------------------------------------

describe('room setup permission', () => {
  it('is only for users with room.manage', async () => {
    api(MANAGER_USER) // General manager: no room.manage
    renderApp('/admin/rooms')

    expect(
      await screen.findByRole('heading', { name: 'You do not have access' }),
    ).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'Admin' })
    expect(within(nav).queryByRole('link', { name: 'Rooms' })).not.toBeInTheDocument()
  })

  it('appears in the admin navigation with room.manage', async () => {
    api()
    renderApp('/admin/rooms')

    const nav = await screen.findByRole('navigation', { name: 'Admin' })
    expect(within(nav).getByRole('link', { name: 'Rooms' })).toHaveAttribute('aria-current', 'page')
    expect(await screen.findByRole('heading', { name: 'Rooms', level: 1 })).toBeInTheDocument()
    expect(document.title).toBe('Rooms – Hotel Management')
  })
})

// --- rooms ---------------------------------------------------------------------------------

describe('rooms', () => {
  it('lists rooms grouped by floor with type, notes and status', async () => {
    api()
    renderApp('/admin/rooms')

    const table = await screen.findByRole('table', { name: 'Rooms by floor' })
    const groups = within(table).getAllByRole('rowgroup').slice(1) // after the header
    expect(groups.map((g) => within(g).getAllByRole('rowheader')[0]?.textContent)).toEqual([
      'Floor 1 (2 rooms)',
      'Floor 2 (1 room)',
    ])
    const row102 = within(table).getByRole('row', { name: /^102/ })
    expect(row102).toHaveTextContent('STD Standard Queen')
    expect(row102).toHaveTextContent('Connecting door to 103')
    expect(row102).toHaveTextContent('Active')
    expect(row102).toHaveTextContent('Guest in house')
  })

  it('reads and writes the filters in the URL', async () => {
    const fetchMock = api(ADMIN_USER, {
      'GET /api/admin/rooms?status=retired&floor=1&room_type_id=3': () => [
        200,
        [room({ id: 9, number: '109', is_active: false, retired_at: '2026-02-01T10:00:00Z' })],
      ],
      'GET /api/admin/rooms?status=active&floor=2': () => [200, [R201]],
    })
    const router = renderApp('/admin/rooms?floor=1&type=3&status=retired')

    expect(await screen.findByRole('row', { name: /^109/ })).toHaveTextContent('Retired')
    expect(screen.getByRole('combobox', { name: 'Floor' })).toHaveValue('1')
    expect(screen.getByRole('combobox', { name: 'Room type' })).toHaveValue('3')
    expect(screen.getByRole('combobox', { name: 'Status' })).toHaveValue('retired')

    const user = userEvent.setup()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Room type' }), 'All room types')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Status' }), 'Active')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Floor' }), 'Floor 2')

    await waitFor(() => {
      expect(router.state.location.search).toBe('?floor=2')
    })
    expect(await screen.findByRole('row', { name: /^201/ })).toBeInTheDocument()
    expect(calls(fetchMock, 'GET /api/admin/rooms?status=active&floor=2')).toBe(1)
  })

  it('adds a room, offering only active room types', async () => {
    const fetchMock = api(ADMIN_USER, {
      'POST /api/admin/rooms': () => [201, room({ id: 105, number: '105' })],
    })
    renderApp('/admin/rooms')
    const user = userEvent.setup()
    const add = await screen.findByRole('button', { name: 'Add room' })

    await user.click(add)
    const dialog = screen.getByRole('dialog', { name: 'Add room' })
    const typeSelect = within(dialog).getByRole('combobox', { name: 'Room type' })
    await waitFor(() => {
      expect(
        within(typeSelect)
          .getAllByRole('option')
          .map((o) => o.textContent),
      ).toEqual(['Choose a room type', 'DLX – Deluxe King', 'STD – Standard Queen'])
    })
    await user.type(within(dialog).getByLabelText('Room number'), '105')
    await user.type(within(dialog).getByLabelText('Floor'), '1')
    await user.selectOptions(typeSelect, 'DLX – Deluxe King')
    await user.type(within(dialog).getByLabelText(/^Notes/), 'Sea view')
    await user.click(within(dialog).getByRole('button', { name: 'Add room' }))

    expect(await screen.findByText('Room 105 was added.')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(add).toHaveFocus()
    expect(bodyOf(fetchMock, 'POST', '/api/admin/rooms')).toEqual({
      number: '105',
      floor: 1,
      room_type_id: 2,
      notes: 'Sea view',
    })
  })

  it('validates the form and shows a taken number on its field', async () => {
    const fetchMock = api(ADMIN_USER, {
      'POST /api/admin/rooms': () => [
        409,
        { detail: 'Room 101 already exists.', code: 'room_number_taken' },
      ],
    })
    renderApp('/admin/rooms')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Add room' }))
    const dialog = screen.getByRole('dialog')

    await user.click(within(dialog).getByRole('button', { name: 'Add room' }))
    expect(within(dialog).getByLabelText('Room number')).toHaveFocus()
    expect(within(dialog).getByLabelText('Room number')).toHaveAccessibleDescription(
      /^Use 1–10 letters/,
    )
    expect(calls(fetchMock, 'POST /api/admin/rooms')).toBe(0)

    await user.type(within(dialog).getByLabelText('Room number'), '101')
    await user.type(within(dialog).getByLabelText('Floor'), '1')
    await user.selectOptions(
      within(dialog).getByRole('combobox', { name: 'Room type' }),
      'STD – Standard Queen',
    )
    await user.click(within(dialog).getByRole('button', { name: 'Add room' }))

    await waitFor(() => {
      expect(within(dialog).getByLabelText('Room number')).toHaveAccessibleDescription(
        'Room 101 already exists.',
      )
    })
  })

  it('edits a room, sending its version and only what changed', async () => {
    const fetchMock = api(ADMIN_USER, {
      'PATCH /api/admin/rooms/101': () => [200, room({ floor: 3, notes: 'Quiet', version: 4 })],
    })
    renderApp('/admin/rooms')
    const user = userEvent.setup()
    const edit = await screen.findByRole('button', { name: 'Edit room 101' })

    await user.click(edit)
    const dialog = screen.getByRole('dialog', { name: 'Edit room 101' })
    const floor = within(dialog).getByLabelText('Floor')
    await user.clear(floor)
    await user.type(floor, '3')
    await user.type(within(dialog).getByLabelText(/^Notes/), 'Quiet')
    await user.click(within(dialog).getByRole('button', { name: 'Save room' }))

    expect(await screen.findByText('Room 101 was saved.')).toBeInTheDocument()
    expect(edit).toHaveFocus()
    expect(bodyOf(fetchMock, 'PATCH', '/api/admin/rooms/101')).toEqual({
      version: 3,
      floor: 3,
      notes: 'Quiet',
    })
  })

  it('locks the room type while a guest is checked in', async () => {
    api()
    renderApp('/admin/rooms')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Edit room 102' }))

    const select = within(screen.getByRole('dialog')).getByRole('combobox', { name: 'Room type' })
    expect(select).toBeDisabled()
    expect(select).toHaveAccessibleDescription(
      'Room 102 has a guest checked in. The room type can be changed after check-out.',
    )
  })

  it('tells the admin when someone else changed the room, and refetches', async () => {
    const fetchMock = api(ADMIN_USER, {
      'PATCH /api/admin/rooms/101': () => [
        409,
        { detail: 'Room 101 was changed by someone else.', code: 'version_conflict' },
      ],
    })
    renderApp('/admin/rooms')
    const user = userEvent.setup()
    const edit = await screen.findByRole('button', { name: 'Edit room 101' })
    const before = calls(fetchMock, 'GET /api/admin/rooms?status=active')

    await user.click(edit)
    await user.type(within(screen.getByRole('dialog')).getByLabelText(/^Notes/), 'x')
    await user.click(screen.getByRole('button', { name: 'Save room' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Room 101 was changed by someone else while you were working on it. The list now shows the latest version; check it and try again.',
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(edit).toHaveFocus()
    await waitFor(() => {
      expect(calls(fetchMock, 'GET /api/admin/rooms?status=active')).toBeGreaterThan(before)
    })
  })

  it('retires a room after a confirmation naming it', async () => {
    const fetchMock = api(ADMIN_USER, {
      'POST /api/admin/rooms/101/retire': () => [200, room({ is_active: false, version: 4 })],
    })
    renderApp('/admin/rooms')
    const user = userEvent.setup()
    const retire = await screen.findByRole('button', { name: 'Retire room 101' })

    await user.click(retire)
    const dialog = screen.getByRole('dialog', { name: 'Retire room 101?' })
    expect(dialog).toHaveAccessibleDescription(/leaves the front desk dashboard/)
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus()
    await user.click(within(dialog).getByRole('button', { name: 'Retire room' }))

    expect(await screen.findByText('Room 101 was retired.')).toBeInTheDocument()
    expect(retire).toHaveFocus()
    expect(bodyOf(fetchMock, 'POST', '/api/admin/rooms/101/retire')).toEqual({ version: 3 })
  })

  it("shows the server's reason when retiring fails, in the dialog", async () => {
    api(ADMIN_USER, {
      'POST /api/admin/rooms/101/retire': () => [
        409,
        { detail: 'Room 101 has a guest checked in.', code: 'room_in_use' },
      ],
    })
    renderApp('/admin/rooms')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Retire room 101' }))
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Retire room' }),
    )

    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent(
      'Room 101 has a guest checked in.',
    )
  })

  it('explains why a room cannot be retired before anyone tries', async () => {
    api()
    renderApp('/admin/rooms')

    const retire = await screen.findByRole('button', { name: 'Retire room 102' })

    expect(retire).toHaveAttribute('aria-disabled', 'true')
    expect(retire).toHaveAccessibleDescription('Room 102 has a guest checked in.')
  })

  it('reactivates a retired room', async () => {
    const retired = room({
      id: 9,
      number: '109',
      is_active: false,
      retired_at: '2026-02-01T10:00:00Z',
    })
    const fetchMock = api(ADMIN_USER, {
      'GET /api/admin/rooms?status=retired': () => [200, [retired]],
      'POST /api/admin/rooms/9/reactivate': () => [200, { ...retired, is_active: true }],
    })
    renderApp('/admin/rooms?status=retired')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Reactivate room 109' }))
    await user.click(
      within(screen.getByRole('dialog', { name: 'Reactivate room 109?' })).getByRole('button', {
        name: 'Reactivate room',
      }),
    )

    expect(await screen.findByText('Room 109 was reactivated.')).toBeInTheDocument()
    expect(bodyOf(fetchMock, 'POST', '/api/admin/rooms/9/reactivate')).toEqual({ version: 3 })
  })

  it('shows empty and error states', async () => {
    let calls = 0
    api(ADMIN_USER, {
      'GET /api/admin/rooms?status=active': () =>
        ++calls <= 3 ? [500, { detail: 'boom' }] : [200, []],
    })
    renderApp('/admin/rooms')

    const alert = await screen.findByRole('alert', {}, { timeout: 5000 })
    expect(alert).toHaveTextContent('The rooms could not be loaded.')
    await userEvent.setup().click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('There are no rooms yet.')).toBeInTheDocument()
  }, 10_000)
})

// --- room types ----------------------------------------------------------------------------

describe('room types', () => {
  it('lists room types with guests, beds, active rooms and status', async () => {
    api()
    renderApp('/admin/rooms/types')

    const table = await screen.findByRole('table', { name: 'Room types' })
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((th) => th.textContent),
    ).toEqual(['Code', 'Name', 'Max guests', 'Beds', 'Active rooms', 'Status', 'Actions'])
    expect(
      within(table)
        .getAllByRole('cell')
        .slice(0, 5)
        .map((c) => c.textContent),
    ).toEqual(['Deluxe King', '3', '1 king', '0', 'Active'])
    expect(screen.getByRole('link', { name: 'Room types' })).toHaveAttribute('aria-current', 'page')
  })

  it('shows retired room types on request, in the URL', async () => {
    const fetchMock = api()
    const router = renderApp('/admin/rooms/types')
    await screen.findByRole('table')

    await userEvent.setup().click(screen.getByLabelText('Show retired room types'))

    expect(await screen.findByRole('row', { name: /^OLD/ })).toHaveTextContent('Retired')
    expect(router.state.location.search).toBe('?show=retired')
    expect(calls(fetchMock, 'GET /api/admin/room-types?include_retired=true')).toBeGreaterThan(0)
  })

  it('adds a room type', async () => {
    const fetchMock = api(ADMIN_USER, {
      'POST /api/admin/room-types': () => [
        201,
        roomType({ id: 4, code: 'FAM', name: 'Family', max_occupancy: 4 }),
      ],
    })
    renderApp('/admin/rooms/types')
    const user = userEvent.setup()
    const add = await screen.findByRole('button', { name: 'Add room type' })

    await user.click(add)
    const dialog = screen.getByRole('dialog', { name: 'Add room type' })
    await user.type(within(dialog).getByLabelText('Code'), 'fam')
    await user.type(within(dialog).getByLabelText('Name'), 'Family')
    const guests = within(dialog).getByLabelText('Max guests')
    await user.clear(guests)
    await user.type(guests, '4')
    await user.type(within(dialog).getByLabelText(/^Beds/), '2 doubles')
    await user.click(within(dialog).getByRole('button', { name: 'Add room type' }))

    expect(await screen.findByText('Room type FAM was added.')).toBeInTheDocument()
    expect(add).toHaveFocus()
    expect(bodyOf(fetchMock, 'POST', '/api/admin/room-types')).toEqual({
      code: 'fam',
      name: 'Family',
      max_occupancy: 4,
      bed_setup: '2 doubles',
      description: null,
    })
  })

  it('checks max guests and puts a taken code on its field', async () => {
    const fetchMock = api(ADMIN_USER, {
      'POST /api/admin/room-types': () => [
        409,
        { detail: 'A room type with the code STD already exists.', code: 'room_type_code_taken' },
      ],
    })
    renderApp('/admin/rooms/types')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Add room type' }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText('Code'), 'STD')
    await user.type(within(dialog).getByLabelText('Name'), 'Another')
    const guests = within(dialog).getByLabelText('Max guests')
    await user.clear(guests)
    await user.type(guests, '20')

    await user.click(within(dialog).getByRole('button', { name: 'Add room type' }))
    expect(guests).toHaveFocus()
    expect(guests).toHaveAccessibleDescription('Enter a number from 1 to 12.')
    expect(calls(fetchMock, 'POST /api/admin/room-types')).toBe(0)

    await user.clear(guests)
    await user.type(guests, '2')
    await user.click(within(dialog).getByRole('button', { name: 'Add room type' }))
    await waitFor(() => {
      expect(within(dialog).getByLabelText('Code')).toHaveAccessibleDescription(
        /^A room type with the code STD already exists\./,
      )
    })
  })

  it('edits a room type, sending only what changed', async () => {
    const fetchMock = api(ADMIN_USER, {
      'PATCH /api/admin/room-types/2': () => [200, { ...DLX, name: 'Deluxe Sea View' }],
    })
    renderApp('/admin/rooms/types')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Edit room type DLX' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit room type DLX' })
    const name = within(dialog).getByLabelText('Name')
    await user.clear(name)
    await user.type(name, 'Deluxe Sea View')
    await user.click(within(dialog).getByRole('button', { name: 'Save room type' }))

    expect(await screen.findByText('Room type DLX was saved.')).toBeInTheDocument()
    expect(bodyOf(fetchMock, 'PATCH', '/api/admin/room-types/2')).toEqual({
      name: 'Deluxe Sea View',
    })
  })

  it('retires a room type after a confirmation naming it', async () => {
    const fetchMock = api(ADMIN_USER, {
      'POST /api/admin/room-types/2/retire': () => [200, { ...DLX, is_active: false }],
    })
    renderApp('/admin/rooms/types')
    const user = userEvent.setup()
    const retire = await screen.findByRole('button', { name: 'Retire room type DLX' })

    await user.click(retire)
    const dialog = screen.getByRole('dialog', { name: 'Retire room type DLX (Deluxe King)?' })
    await user.click(within(dialog).getByRole('button', { name: 'Retire room type' }))

    expect(await screen.findByText('Room type DLX was retired.')).toBeInTheDocument()
    expect(retire).toHaveFocus()
    expect(calls(fetchMock, 'POST /api/admin/room-types/2/retire')).toBe(1)
  })

  it('explains why a room type with rooms cannot be retired', async () => {
    api()
    renderApp('/admin/rooms/types')

    const retire = await screen.findByRole('button', { name: 'Retire room type STD' })

    expect(retire).toHaveAttribute('aria-disabled', 'true')
    expect(retire).toHaveAccessibleDescription(
      'Room type STD still has 2 active rooms. Retire them or give them another type first.',
    )
  })

  it('shows an empty state', async () => {
    api(ADMIN_USER, { 'GET /api/admin/room-types': () => [200, []] })
    renderApp('/admin/rooms/types')

    expect(
      await screen.findByText('There are no active room types. Add one to start setting up rooms.'),
    ).toBeInTheDocument()
  })
})
