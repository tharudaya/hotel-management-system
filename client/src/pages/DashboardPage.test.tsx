import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { DashboardRoom } from '../api/types'
import {
  HOTEL,
  makeReservation,
  ROOMS,
  SUMMARY,
  BUSINESS_DATE,
  fixtureRoom,
} from '../test/fixtures'
import {
  FRONT_DESK_USER,
  HOUSEKEEPING_SUPERVISOR_USER,
  MANAGER_USER,
  ROOM_ATTENDANT_USER,
  mockApi,
  renderApp,
} from '../test/renderApp'

type Handler = () => [number, unknown?]

const MANAGER = MANAGER_USER

/** Fake backend for the dashboard; `rooms` can be swapped to simulate server-side changes. */
function dashboardApi(extra: Record<string, Handler> = {}, user: object = FRONT_DESK_USER) {
  const state = { rooms: ROOMS, roomsRequests: 0 }
  const fetchMock = mockApi({
    'GET /api/auth/me': () => [200, user],
    'GET /api/hotel': () => [200, HOTEL],
    'GET /api/dashboard/summary': () => [200, SUMMARY],
    'GET /api/dashboard/rooms': () => {
      state.roomsRequests += 1
      return [200, { business_date: BUSINESS_DATE, rooms: state.rooms }]
    },
    'GET /api/reservations/13': () => [200, makeReservation()],
    'GET /api/reservations/11': () => [
      200,
      makeReservation({
        id: 11,
        guest: { first_name: 'Ada', last_name: 'Lovelace', email: null, phone: null },
      }),
    ],
    ...extra,
  })
  return { fetchMock, state }
}

function tileNumbers(): string[] {
  return screen
    .getAllByRole('button', { name: /^Room \d+/ })
    .map((tile) => tile.getAttribute('aria-label')?.split(',')[0]?.replace('Room ', '') ?? '')
}

async function openRoom(number: string) {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: new RegExp(`^Room ${number},`) }))
  return screen.getByRole('complementary', { name: `Room ${number}` })
}

describe('dashboard layout', () => {
  it('shows hotel, business date, user, summary and rooms grouped by floor', async () => {
    dashboardApi()
    renderApp('/')

    const header = await screen.findByRole('banner')
    expect(await within(header).findByText('Grand Hotel Stockholm')).toBeInTheDocument()
    expect(header).toHaveTextContent('Business date Mon, 12 Oct 2026')
    expect(header).toHaveTextContent('Fran Desk')
    expect(within(header).getByRole('button', { name: 'Sign out' })).toBeInTheDocument()

    const summary = await screen.findByRole('list', { name: "Today's summary" })
    expect(within(summary).getByRole('button', { name: /Arrivals/ })).toHaveTextContent('1/4')
    expect(within(summary).getByRole('button', { name: /Dirty/ })).toHaveTextContent('3')
    expect(summary).toHaveTextContent('80%')

    const floor1 = screen.getByRole('region', { name: /Floor 1/ })
    const floor2 = screen.getByRole('region', { name: /Floor 2/ })
    expect(within(floor1).getAllByRole('button')).toHaveLength(3)
    expect(within(floor2).getAllByRole('button')).toHaveLength(3)
    expect(screen.getByText('Showing 6 of 6 rooms')).toBeInTheDocument()
  })
})

describe('filtering', () => {
  it('filters by floor and reflects it in the URL', async () => {
    dashboardApi()
    const router = renderApp('/')
    const user = userEvent.setup()

    await user.selectOptions(await screen.findByLabelText('Floor'), 'Floor 2')

    expect(tileNumbers()).toEqual(['201', '202', '203'])
    expect(router.state.location.search).toBe('?floor=2')
    expect(screen.getByText('Showing 3 of 6 rooms')).toBeInTheDocument()
  })

  it('filters by room type, occupancy and housekeeping status', async () => {
    dashboardApi()
    renderApp('/')
    const user = userEvent.setup()

    await user.selectOptions(await screen.findByLabelText('Room type'), 'Deluxe King')
    expect(tileNumbers()).toEqual(['103', '202'])

    await user.selectOptions(screen.getByLabelText('Room type'), 'All')
    await user.selectOptions(screen.getByLabelText('Occupancy'), 'Arriving')
    await user.selectOptions(screen.getByLabelText('Housekeeping'), 'Dirty')
    expect(tileNumbers()).toEqual(['203'])
  })

  it('searches by room number or guest name', async () => {
    dashboardApi()
    renderApp('/')
    const user = userEvent.setup()
    const search = await screen.findByLabelText('Room or guest')

    await user.type(search, 'hopper')
    expect(tileNumbers()).toEqual(['103'])

    await user.clear(search)
    await user.type(search, '20')
    expect(tileNumbers()).toEqual(['201', '202', '203'])
  })

  it('clicking a summary count filters the grid and clicking again clears it', async () => {
    dashboardApi()
    renderApp('/')
    const user = userEvent.setup()
    const dirty = await screen.findByRole('button', { name: /^Dirty/ })

    await user.click(dirty)
    expect(dirty).toHaveAttribute('aria-pressed', 'true')
    expect(tileNumbers()).toEqual(['102', '201', '203'])

    await user.click(screen.getByRole('button', { name: /^Arrivals/ }))
    expect(dirty).toHaveAttribute('aria-pressed', 'false')
    expect(tileNumbers()).toEqual(['103', '201', '203'])

    await user.click(screen.getByRole('button', { name: /^Arrivals/ }))
    expect(tileNumbers()).toHaveLength(6)
  })

  it('restores filters from the URL and shows an empty state that clears them', async () => {
    dashboardApi()
    const router = renderApp('/?floor=1&q=turing')
    const user = userEvent.setup()

    expect(await screen.findByText('No rooms match these filters.')).toBeInTheDocument()
    expect(screen.getByLabelText('Floor')).toHaveValue('1')
    expect(screen.getByLabelText('Room or guest')).toHaveValue('turing')

    const emptyState = screen.getByText('No rooms match these filters.').closest('div')
    if (!emptyState) throw new Error('empty state not found')
    await user.click(within(emptyState).getByRole('button', { name: 'Clear filters' }))

    expect(tileNumbers()).toHaveLength(6)
    expect(router.state.location.search).toBe('')
  })
})

describe('room detail panel', () => {
  it('opens with reservation details and closes with Escape, returning focus', async () => {
    dashboardApi()
    const router = renderApp('/')
    const user = userEvent.setup()

    const panel = await openRoom('201')

    expect(router.state.location.search).toBe('?room=4')
    expect(within(panel).getByRole('heading', { name: 'Room 201' })).toHaveFocus()
    expect(await within(panel).findByText('Alan Turing')).toBeInTheDocument()
    expect(panel).toHaveTextContent('TUR1NG42')
    expect(panel).toHaveTextContent('Sat, 10 Oct 2026 – Mon, 12 Oct 2026 (2 nights)')
    expect(panel).toHaveTextContent('Due out today')
    expect(panel).toHaveTextContent('Another guest arrives today')

    await user.keyboard('{Escape}')

    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^Room 201,/ })).toHaveFocus()
    })
  })

  it('opens the room given in the URL', async () => {
    dashboardApi()
    renderApp('/?room=2')

    const panel = await screen.findByRole('complementary', { name: 'Room 102' })
    expect(await within(panel).findByText('Ada Lovelace')).toBeInTheDocument()
  })
})

describe('room actions', () => {
  it('checks a guest out and refreshes the room', async () => {
    const checkedOut: DashboardRoom = {
      ...fixtureRoom(4),
      occupancy_status: 'arriving_today',
      guest_name: 'Next Guest',
      reservation_id: 99,
      version: 2,
    }
    const { fetchMock, state } = dashboardApi({
      'POST /api/reservations/13/check-out': () => {
        state.rooms = ROOMS.map((r) => (r.room_id === 4 ? checkedOut : r))
        return [200, checkedOut]
      },
      'GET /api/reservations/99': () => [200, makeReservation({ id: 99, status: 'confirmed' })],
    })
    renderApp('/')
    const user = userEvent.setup()
    const panel = await openRoom('201')

    await user.click(within(panel).getByRole('button', { name: 'Check out' }))

    expect(await within(panel).findByRole('status')).toHaveTextContent(
      'Alan Turing checked out of room 201.',
    )
    const call = fetchMock.mock.calls.find(([p]) => p === '/api/reservations/13/check-out')
    expect(call?.[1]?.body).toBe('{"room_version":1}')
    expect(screen.getByRole('button', { name: /^Room 201,/ })).toHaveAccessibleName(
      /arriving today/,
    )
  })

  it('shows a pending state while the action runs', async () => {
    let respond: (reply: [number, unknown]) => void = () => undefined
    dashboardApi({
      'POST /api/reservations/13/check-out': () =>
        new Promise((resolve) => {
          respond = resolve
        }) as unknown as [number, unknown],
    })
    renderApp('/')
    const user = userEvent.setup()
    const panel = await openRoom('201')

    await user.click(within(panel).getByRole('button', { name: 'Check out' }))

    expect(within(panel).getByRole('button', { name: 'Checking out…' })).toBeDisabled()
    respond([200, fixtureRoom(4)])
    expect(await within(panel).findByRole('button', { name: 'Check out' })).toBeEnabled()
  })

  it('on a 409 conflict refetches and says the room was changed by someone else', async () => {
    const { state } = dashboardApi({
      'POST /api/reservations/13/check-out': () => {
        // Someone else already checked the guest out.
        state.rooms = ROOMS.map((r) =>
          r.room_id === 4 ? { ...r, occupancy_status: 'vacant', guest_name: null, version: 5 } : r,
        )
        return [409, { detail: 'Room 201 was changed by someone else.', code: 'version_conflict' }]
      },
    })
    renderApp('/')
    const user = userEvent.setup()
    const panel = await openRoom('201')
    const requestsBefore = state.roomsRequests

    await user.click(within(panel).getByRole('button', { name: 'Check out' }))

    expect(await within(panel).findByRole('alert')).toHaveTextContent(
      'Room 201 was changed by someone else. The latest details are now shown',
    )
    await waitFor(() => {
      expect(state.roomsRequests).toBeGreaterThan(requestsBefore)
    })
    expect(await screen.findByRole('button', { name: /^Room 201, vacant/ })).toBeInTheDocument()
  })

  it('shows business-rule conflicts from the server', async () => {
    dashboardApi({
      'POST /api/reservations/12/check-in': () => [
        409,
        { detail: 'Room 103 is not of the reserved room type.', code: 'room_type_mismatch' },
      ],
      'GET /api/reservations/12': () => [200, makeReservation({ id: 12, status: 'confirmed' })],
    })
    renderApp('/')
    const user = userEvent.setup()
    const panel = await openRoom('103')

    await user.click(within(panel).getByRole('button', { name: 'Check in' }))

    expect(await within(panel).findByRole('alert')).toHaveTextContent(
      'Room 103 is not of the reserved room type.',
    )
  })

  it('disables check-in until the room is cleaned (inspection or ready)', async () => {
    dashboardApi({
      'GET /api/reservations/14': () => [200, makeReservation({ id: 14, status: 'confirmed' })],
    })
    renderApp('/')
    const panel = await openRoom('203')

    const checkIn = within(panel).getByRole('button', { name: 'Check in' })
    expect(checkIn).toBeDisabled()
    expect(checkIn).toHaveAccessibleDescription(
      'The room must be cleaned (awaiting inspection or ready) before check-in.',
    )
  })

  it('offers housekeeping changes to managers but not to front desk', async () => {
    const { fetchMock } = dashboardApi(
      {
        'PATCH /api/rooms/1/housekeeping-status': () => [
          200,
          { ...fixtureRoom(1), housekeeping_status: 'dirty', version: 2 },
        ],
      },
      MANAGER,
    )
    renderApp('/')
    const user = userEvent.setup()
    const panel = await openRoom('101')

    // Ready (inspected) -> dirty or out of order, per the transition table
    const buttons = within(panel)
      .getAllByRole('button', { name: /^Mark/ })
      .map((b) => b.textContent)
    expect(buttons).toEqual(['Mark dirty', 'Mark out of order'])

    await user.click(within(panel).getByRole('button', { name: 'Mark dirty' }))

    expect(await within(panel).findByRole('status')).toHaveTextContent('Room 101 marked dirty.')
    const call = fetchMock.mock.calls.find(([p]) => p === '/api/rooms/1/housekeeping-status')
    expect(call?.[1]?.body).toBe('{"status":"dirty","version":1}')
  })

  it('names housekeeping actions for what they do', async () => {
    dashboardApi(
      {
        'PATCH /api/rooms/2/housekeeping-status': () => [
          200,
          { ...fixtureRoom(2), housekeeping_status: 'clean', version: 2 },
        ],
        'GET /api/reservations/11': () => [200, makeReservation({ id: 11 })],
      },
      MANAGER,
    )
    renderApp('/')
    const user = userEvent.setup()
    const panel = await openRoom('102') // dirty

    const buttons = within(panel)
      .getAllByRole('group', { name: 'Housekeeping' })[0]
      ?.querySelectorAll('button')
    expect([...(buttons ?? [])].map((b) => b.textContent)).toEqual([
      'Start cleaning',
      'Send to inspection',
      'Mark out of order',
    ])

    await user.click(within(panel).getByRole('button', { name: 'Send to inspection' }))

    expect(await within(panel).findByRole('status')).toHaveTextContent(
      'Room 102 sent to inspection.',
    )
  })

  it('offers front desk housekeeping changes but not out of order', async () => {
    dashboardApi()
    renderApp('/')

    const panel = await openRoom('101') // Ready: dirty or out of order

    const group = within(panel).getByRole('group', { name: 'Housekeeping' })
    expect(
      within(group)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['Mark dirty'])
  })

  it('hides out-of-order rooms from roles that cannot return them to service', async () => {
    dashboardApi()
    renderApp('/')

    const panel = await openRoom('202') // out of order

    expect(within(panel).queryByRole('group', { name: 'Housekeeping' })).not.toBeInTheDocument()
  })

  it('shows housekeeping supervisors no reservation details or guest actions', async () => {
    dashboardApi({}, HOUSEKEEPING_SUPERVISOR_USER)
    renderApp('/')

    const panel = await openRoom('201') // departing guest, dirty

    expect(panel).toHaveTextContent(
      'Alan Turing is booked in this room. Your role does not include reservation details.',
    )
    expect(within(panel).queryByRole('button', { name: /Check/ })).not.toBeInTheDocument()
    const group = within(panel).getByRole('group', { name: 'Housekeeping' })
    expect(
      within(group)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['Start cleaning', 'Send to inspection', 'Mark out of order'])
  })

  it('tells roles without dashboard access instead of failing', async () => {
    mockApi({
      'GET /api/auth/me': () => [200, ROOM_ATTENDANT_USER],
      'GET /api/hotel': () => [200, HOTEL],
    })
    renderApp('/')

    expect(await screen.findByRole('heading', { name: 'No dashboard access' })).toBeInTheDocument()
    expect(screen.getByText(/Your role \(Room attendant\)/)).toBeInTheDocument()
  })
})
