import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { ReservationListItem, ReservationPage, ReservationView } from '../api/types'
import { BUSINESS_DATE, HOTEL, makeReservationView, makeRoom, SUMMARY } from '../test/fixtures'
import {
  FRONT_DESK_USER,
  FRONT_OFFICE_MANAGER_USER,
  HOUSEKEEPING_SUPERVISOR_USER,
  mockApi,
  renderApp,
} from '../test/renderApp'
import { parseAmount } from '../lib/format'
import { DEFAULT_PARAMS, type ReservationListParams, toApiQuery } from './params'

// --- fixtures -----------------------------------------------------------------------------

function item(id: number, overrides: Partial<ReservationListItem> = {}): ReservationListItem {
  return {
    id,
    confirmation_code: `CODE${id}234`.slice(0, 8),
    guest_name: `Guest ${id}`,
    check_in_date: BUSINESS_DATE,
    check_out_date: '2026-10-14',
    nights: 2,
    adults: 1,
    children: 0,
    room_type: 'STD',
    room_type_name: 'Standard Queen',
    room_number: null,
    status: 'confirmed',
    source: 'phone',
    is_vip: false,
    total_minor: 259_800,
    currency: 'SEK',
    ...overrides,
  }
}

const ADA = item(21, {
  confirmation_code: 'ADAX2345',
  guest_name: 'Ada Lovelace',
  is_vip: true,
  source: 'walk_in',
})
const GRACE = item(22, {
  confirmation_code: 'GRAC2345',
  guest_name: 'Grace Hopper',
  room_number: '102',
  status: 'checked_in',
  check_in_date: '2026-10-10',
})

function page(items: ReservationListItem[], total = items.length, pageNumber = 1): ReservationPage {
  return { items, total, page: pageNumber, page_size: 25 }
}

const listKey = (changes: Partial<ReservationListParams> = {}) =>
  `GET /api/reservations?${toApiQuery({ ...DEFAULT_PARAMS, ...changes })}`

type Handler = () => [number, unknown]

function base(me = FRONT_DESK_USER): Record<string, Handler> {
  return {
    'GET /api/auth/me': () => [200, me],
    'GET /api/hotel': () => [200, HOTEL],
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

// Arriving today, no room yet.
const ARRIVING = makeReservationView({
  id: 31,
  confirmation_code: 'KX7M2P9Q',
  status: 'confirmed',
  check_in_date: BUSINESS_DATE,
  check_out_date: '2026-10-14',
  room_id: null,
  room_number: null,
  guest: {
    id: 9,
    first_name: 'Ada',
    last_name: 'Lovelace',
    email: 'ada@example.com',
    phone: '+46701234567',
    contact_hidden: false,
  },
  nightly_rates: [
    { night_date: '2026-10-12', rate_minor: 129_900 },
    { night_date: '2026-10-13', rate_minor: 99_950 },
  ],
  total_minor: 229_850,
  notes: 'Late arrival',
  is_vip: true,
  history: [
    {
      action: 'reservation.created',
      occurred_at: '2026-10-01T08:00:00Z',
      actor: { id: 1, full_name: 'Fran Desk' },
      status_before: null,
      status_after: 'confirmed',
      changed: ['status'],
      reason: null,
    },
  ],
})

function detailHandlers(view: ReservationView, me = FRONT_DESK_USER): Record<string, Handler> {
  return { ...base(me), [`GET /api/reservations/${view.id}`]: () => [200, view] }
}

// --- navigation ---------------------------------------------------------------------------

describe('reservations navigation', () => {
  it('shows "Reservations" in the main navigation with reservation.view', async () => {
    mockApi({ ...base(), [listKey()]: () => [200, page([ADA])] })
    renderApp('/reservations')

    const nav = await screen.findByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: 'Reservations' })).toHaveAttribute(
      'href',
      '/reservations',
    )
    expect(await screen.findByRole('heading', { name: 'Reservations', level: 1 })).toBeVisible()
  })

  it('hides it and refuses the page without reservation.view', async () => {
    mockApi({ ...base(HOUSEKEEPING_SUPERVISOR_USER) })
    renderApp('/reservations')

    expect(await screen.findByText(/You do not have access/)).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).queryByRole('link', { name: 'Reservations' })).not.toBeInTheDocument()
  })
})

// --- list ---------------------------------------------------------------------------------

describe('reservations list', () => {
  it('shows a sortable table with status text, room and source', async () => {
    mockApi({ ...base(), [listKey()]: () => [200, page([ADA, GRACE])] })
    renderApp('/reservations')

    const table = await screen.findByRole('table', {
      name: /Reservations, sorted by arrival, ascending/,
    })
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((th) => th.textContent),
    ).toEqual([
      'Code',
      'Guest',
      'Arrival',
      'Departure',
      'Nights',
      'Room type',
      'Room',
      'Status',
      'Source',
    ])
    const ada = within(table).getByRole('row', { name: /Ada Lovelace/ })
    expect(within(ada).getByRole('link', { name: /Ada Lovelace/ })).toHaveAttribute(
      'href',
      '/reservations/21',
    )
    expect(ada).toHaveTextContent('ADAX2345')
    expect(ada).toHaveTextContent('Mon, 12 Oct 2026')
    expect(ada).toHaveTextContent('Not assigned')
    expect(ada).toHaveTextContent('Confirmed')
    expect(ada).toHaveTextContent('Walk-in')
    expect(ada).toHaveTextContent('VIP')
    const grace = within(table).getByRole('row', { name: /Grace Hopper/ })
    expect(grace).toHaveTextContent('102')
    expect(grace).toHaveTextContent('Checked in')
    expect(screen.getByRole('columnheader', { name: /Arrival/ })).toHaveAttribute(
      'aria-sort',
      'ascending',
    )
  })

  it('reads every filter, sort and page from the URL', async () => {
    const params = {
      q: 'ada',
      status: 'confirmed',
      view: 'arriving_today',
      dates: 'departing',
      from: '2026-10-12',
      to: '2026-10-20',
      sort: '-guest',
      page: 2,
    } as const
    mockApi({ ...base(), [listKey(params)]: () => [200, page([ADA], 26, 2)] })
    renderApp(
      '/reservations?q=ada&status=confirmed&view=arriving_today&dates=departing&from=2026-10-12&to=2026-10-20&sort=-guest&page=2',
    )

    await screen.findByRole('link', { name: /Ada Lovelace/ })
    expect(screen.getByRole('searchbox', { name: 'Search' })).toHaveValue('ada')
    expect(screen.getByRole('combobox', { name: 'Status' })).toHaveValue('confirmed')
    expect(screen.getByRole('combobox', { name: 'Dates apply to' })).toHaveValue('departing')
    expect(screen.getByLabelText('From')).toHaveValue('2026-10-12')
    expect(screen.getByLabelText('To')).toHaveValue('2026-10-20')
    expect(screen.getByRole('button', { name: 'Arriving today' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByRole('columnheader', { name: /Guest/ })).toHaveAttribute(
      'aria-sort',
      'descending',
    )
    expect(screen.getByText('Page 2 of 2')).toBeInTheDocument()
  })

  it('quick filters, status and dates update the URL and the request', async () => {
    const fetchMock = mockApi({
      ...base(),
      [listKey()]: () => [200, page([ADA, GRACE])],
      [listKey({ view: 'in_house' })]: () => [200, page([GRACE])],
      [listKey({ view: 'in_house', status: 'checked_in' })]: () => [200, page([GRACE])],
      [listKey({ view: 'in_house', status: 'checked_in', dates: 'arriving' })]: () => [
        200,
        page([GRACE]),
      ],
      [listKey({ view: 'in_house', status: 'checked_in', dates: 'arriving', from: '2026-10-10' })]:
        () => [200, page([GRACE])],
      [listKey({ status: 'checked_in', dates: 'arriving', from: '2026-10-10' })]: () => [
        200,
        page([GRACE]),
      ],
    })
    const router = renderApp('/reservations')
    const user = userEvent.setup()
    await screen.findByRole('link', { name: /Ada Lovelace/ })

    await user.click(screen.getByRole('button', { name: 'In house' }))
    await waitFor(() => {
      expect(router.state.location.search).toBe('?view=in_house')
    })
    expect(screen.getByRole('button', { name: 'In house' })).toHaveAttribute('aria-pressed', 'true')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Status' }), 'checked_in')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Dates apply to' }), 'arriving')
    await user.type(screen.getByLabelText('From'), '2026-10-10')
    // Pressing the same quick filter again turns it off.
    await user.click(screen.getByRole('button', { name: 'In house' }))

    await waitFor(() => {
      expect(router.state.location.search).toBe('?status=checked_in&dates=arriving&from=2026-10-10')
    })
    expect(requested(fetchMock)).toContain(
      listKey({ status: 'checked_in', dates: 'arriving', from: '2026-10-10' }).slice(4),
    )
  })

  it('searches by code or name after a pause in typing', async () => {
    const fetchMock = mockApi({
      ...base(),
      [listKey()]: () => [200, page([ADA, GRACE])],
      [listKey({ q: 'kx7m' })]: () => [200, page([ADA])],
    })
    const router = renderApp('/reservations')
    const user = userEvent.setup()
    await screen.findByRole('link', { name: /Ada Lovelace/ })

    await user.type(screen.getByRole('searchbox', { name: 'Search' }), 'kx7m')

    await waitFor(() => {
      expect(router.state.location.search).toBe('?q=kx7m')
    })
    // One request for the finished word, not one per key.
    await waitFor(() => {
      expect(requested(fetchMock).filter((p) => p.includes('q='))).toEqual([
        listKey({ q: 'kx7m' }).slice(4),
      ])
    })
  })

  it('reads a date range typed backwards the right way round', () => {
    const query = new URLSearchParams(
      toApiQuery({ ...DEFAULT_PARAMS, dates: 'arriving', from: '2026-10-20', to: '2026-10-12' }),
    )
    expect([query.get('date_from'), query.get('date_to')]).toEqual(['2026-10-12', '2026-10-20'])
  })

  it('offers to clear filters when nothing matches', async () => {
    mockApi({
      ...base(),
      [listKey({ status: 'no_show' })]: () => [200, page([])],
      [listKey()]: () => [200, page([ADA])],
    })
    const router = renderApp('/reservations?status=no_show')
    const user = userEvent.setup()

    expect(await screen.findByText('No reservations match these filters.')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Clear filters' }))

    await waitFor(() => {
      expect(router.state.location.search).toBe('')
    })
    expect(await screen.findByRole('link', { name: /Ada Lovelace/ })).toBeVisible()
  })

  it('offers "New reservation" only with reservation.create', async () => {
    const viewer = {
      ...FRONT_DESK_USER,
      permissions: FRONT_DESK_USER.permissions.filter((p) => p !== 'reservation.create'),
    }
    mockApi({ ...base(viewer), [listKey()]: () => [200, page([ADA])] })
    renderApp('/reservations')

    await screen.findByRole('table')
    expect(screen.queryByRole('link', { name: 'New reservation' })).not.toBeInTheDocument()
  })
})

// --- detail -------------------------------------------------------------------------------

describe('reservation detail', () => {
  it('shows guest, stay, prices in the hotel currency, room, notes and history', async () => {
    mockApi(detailHandlers(ARRIVING))
    renderApp('/reservations/31')

    expect(await screen.findByRole('heading', { name: 'Ada Lovelace', level: 1 })).toBeVisible()
    expect(screen.getByText('KX7M2P9Q')).toBeVisible()
    expect(screen.getAllByText('VIP')[0]).toBeVisible()
    const guest = screen.getByRole('region', { name: 'Guest' })
    expect(guest).toHaveTextContent('ada@example.com')
    expect(guest).toHaveTextContent('+46701234567')
    const price = screen.getByRole('table', { name: 'Price per night' })
    expect(price).toHaveTextContent('SEK 1,299.00')
    expect(price).toHaveTextContent('SEK 999.50')
    expect(within(price).getByRole('rowheader', { name: 'Total' }).closest('tr')).toHaveTextContent(
      'SEK 2,298.50',
    )
    expect(screen.getByRole('region', { name: 'Room' })).toHaveTextContent('No room assigned yet')
    expect(screen.getByRole('region', { name: 'Notes' })).toHaveTextContent('Late arrival')
    const history = screen.getByRole('region', { name: 'History' })
    expect(history).toHaveTextContent('Reservation made')
    expect(history).toHaveTextContent('by Fran Desk')
  })

  it('hides contact details without guest.view_contact', async () => {
    mockApi(
      detailHandlers({
        ...ARRIVING,
        guest: { ...ARRIVING.guest, email: null, phone: null, contact_hidden: true },
      }),
    )
    renderApp('/reservations/31')

    const guest = await screen.findByRole('region', { name: 'Guest' })
    expect(guest).toHaveTextContent(/Hidden: your role does not include/)
    expect(guest).not.toHaveTextContent('Email')
  })

  it('says so when the reservation does not exist', async () => {
    mockApi({
      ...base(),
      'GET /api/reservations/99': () => [404, { detail: 'Not found', code: 'not_found' }],
    })
    renderApp('/reservations/99')

    expect(await screen.findByRole('heading', { name: 'Reservation not found' })).toBeVisible()
  })
})

// --- actions by permission and status -----------------------------------------------------

async function actionNames(): Promise<string[]> {
  const group = await screen.findByRole('group', { name: 'Actions' })
  return within(group)
    .getAllByRole('button')
    .map((b) => b.textContent)
}

describe('reservation actions', () => {
  it('a receptionist can check in, assign and change, but not cancel', async () => {
    mockApi(detailHandlers(ARRIVING))
    renderApp('/reservations/31')

    expect(await actionNames()).toEqual(['Check in', 'Assign room', 'Change booking'])
  })

  it('a front office manager can also cancel and mark a no-show', async () => {
    mockApi(detailHandlers(ARRIVING, FRONT_OFFICE_MANAGER_USER))
    renderApp('/reservations/31')

    expect(await actionNames()).toEqual([
      'Check in',
      'Assign room',
      'Change booking',
      'Mark no-show',
      'Cancel booking',
    ])
  })

  it('a future booking cannot be checked in or marked as a no-show yet', async () => {
    mockApi(
      detailHandlers(
        { ...ARRIVING, check_in_date: '2026-10-20', check_out_date: '2026-10-22' },
        FRONT_OFFICE_MANAGER_USER,
      ),
    )
    renderApp('/reservations/31')

    expect(await actionNames()).toEqual(['Assign room', 'Change booking', 'Cancel booking'])
  })

  it('a guest in house can be checked out or have the booking changed', async () => {
    mockApi(
      detailHandlers(
        makeReservationView({ id: 31, status: 'checked_in', check_out_date: '2026-10-14' }),
        FRONT_OFFICE_MANAGER_USER,
      ),
    )
    renderApp('/reservations/31')

    expect(await actionNames()).toEqual(['Check out', 'Change booking'])
  })

  it('a finished booking offers no actions', async () => {
    mockApi(detailHandlers({ ...ARRIVING, status: 'cancelled' }, FRONT_OFFICE_MANAGER_USER))
    renderApp('/reservations/31')

    await screen.findByRole('heading', { name: 'Ada Lovelace', level: 1 })
    expect(screen.queryByRole('group', { name: 'Actions' })).not.toBeInTheDocument()
  })
})

describe('cancelling', () => {
  it('names the guest and code, needs a reason, and shows the result', async () => {
    const cancelled = {
      ...ARRIVING,
      status: 'cancelled' as const,
      version: 2,
      history: [
        ...ARRIVING.history,
        {
          action: 'reservation.cancelled',
          occurred_at: '2026-10-12T09:00:00Z',
          actor: { id: 6, full_name: 'Flo Office' },
          status_before: 'confirmed' as const,
          status_after: 'cancelled' as const,
          changed: ['status'],
          reason: 'Flight cancelled',
        },
      ],
    }
    const fetchMock = mockApi({
      ...detailHandlers(ARRIVING, FRONT_OFFICE_MANAGER_USER),
      'POST /api/reservations/31/cancel': () => [200, cancelled],
      'GET /api/reservations?sort=check_in_date&page=1&page_size=25': () => [200, page([])],
    })
    renderApp('/reservations/31')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Cancel booking' }))
    const dialog = screen.getByRole('dialog', { name: "Cancel Ada Lovelace's booking?" })
    expect(dialog).toHaveTextContent('KX7M2P9Q')
    // Focus starts on the safe choice.
    expect(within(dialog).getByRole('button', { name: 'Keep booking' })).toHaveFocus()
    await user.click(within(dialog).getByRole('button', { name: 'Cancel booking' }))
    expect(within(dialog).getByText('Enter why the booking is cancelled.')).toBeVisible()
    expect(requested(fetchMock, 'POST')).toEqual([])

    await user.type(within(dialog).getByLabelText('Reason'), 'Flight cancelled')
    await user.click(within(dialog).getByRole('button', { name: 'Cancel booking' }))

    expect(await screen.findByRole('status', { name: '' })).toBeDefined()
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    expect(bodyOf(fetchMock, '/api/reservations/31/cancel')).toEqual({
      version: 1,
      reason: 'Flight cancelled',
    })
    expect(screen.getByText('Ada Lovelace (KX7M2P9Q) has been cancelled.')).toBeVisible()
    expect(screen.getByRole('region', { name: 'History' })).toHaveTextContent(
      'Reason: Flight cancelled',
    )
    expect(screen.queryByRole('group', { name: 'Actions' })).not.toBeInTheDocument()
  })

  it('marks a no-show after a confirmation that names the guest', async () => {
    const fetchMock = mockApi({
      ...detailHandlers(ARRIVING, FRONT_OFFICE_MANAGER_USER),
      'POST /api/reservations/31/no-show': () => [
        200,
        { ...ARRIVING, status: 'no_show', version: 2 },
      ],
    })
    renderApp('/reservations/31')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Mark no-show' }))
    const dialog = screen.getByRole('dialog', { name: 'Mark Ada Lovelace as a no-show?' })
    expect(dialog).toHaveTextContent('KX7M2P9Q')
    await user.click(within(dialog).getByRole('button', { name: 'Mark no-show' }))

    expect(
      await screen.findByText('Ada Lovelace (KX7M2P9Q) has been marked as a no-show.'),
    ).toBeVisible()
    expect(bodyOf(fetchMock, '/api/reservations/31/no-show')).toEqual({ version: 1 })
  })
})

describe('conflicts', () => {
  it('refetches and explains when someone else changed the booking', async () => {
    let reads = 0
    const changedElsewhere = { ...ARRIVING, version: 2, notes: 'Changed by a colleague' }
    const fetchMock = mockApi({
      ...base(),
      'GET /api/reservations/31': () => [200, ++reads === 1 ? ARRIVING : changedElsewhere],
      'PATCH /api/reservations/31': () => [
        409,
        { detail: 'Reservation KX7M2P9Q was changed by someone else', code: 'version_conflict' },
      ],
      'GET /api/reservations/availability?check_in=2026-10-12&check_out=2026-10-14&adults=1&children=0':
        () => [
          200,
          {
            check_in_date: '2026-10-12',
            check_out_date: '2026-10-14',
            nights: 2,
            adults: 1,
            children: 0,
            currency: 'SEK',
            room_types: [],
          },
        ],
    })
    renderApp('/reservations/31')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Change booking' }))
    const dialog = screen.getByRole('dialog', { name: 'Change booking KX7M2P9Q' })
    const notes = within(dialog).getByLabelText(/Notes/)
    await user.clear(notes)
    await user.type(notes, 'Cot needed')
    await user.click(within(dialog).getByRole('button', { name: 'Save changes' }))

    const alert = (await screen.findByText(/Someone else changed this booking/)).closest(
      '[role="alert"]',
    )
    expect(alert).not.toBeNull()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    // The latest version is shown.
    expect(await screen.findByText('Changed by a colleague')).toBeVisible()
    expect(bodyOf(fetchMock, '/api/reservations/31')).toEqual({ version: 1, notes: 'Cot needed' })
    expect(reads).toBe(2)
  })

  it('shows other refusals inside the dialog', async () => {
    mockApi({
      ...detailHandlers(ARRIVING),
      'GET /api/dashboard/rooms': () => [
        200,
        {
          business_date: BUSINESS_DATE,
          rooms: [makeRoom({ room_id: 5, number: '105', housekeeping_status: 'inspected' })],
        },
      ],
      'POST /api/reservations/31/assign-room': () => [
        409,
        {
          detail: 'Room 105 is taken by another guest on some of these nights.',
          code: 'room_not_available',
        },
      ],
    })
    renderApp('/reservations/31')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Assign room' }))
    const dialog = screen.getByRole('dialog', { name: /Assign a room to Ada Lovelace/ })
    await user.selectOptions(await within(dialog).findByRole('combobox', { name: /room/ }), '5')
    await user.click(within(dialog).getByRole('button', { name: 'Assign room' }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Room 105 is taken by another guest on some of these nights.',
    )
  })
})

// --- dashboard links ----------------------------------------------------------------------

describe('dashboard links to reservations', () => {
  it('the arrivals list links each booking', async () => {
    mockApi({
      ...base(),
      'GET /api/dashboard/rooms': () => [200, { business_date: BUSINESS_DATE, rooms: [] }],
      'GET /api/dashboard/summary': () => [
        200,
        {
          ...SUMMARY,
          arrivals: [
            {
              reservation_id: 31,
              confirmation_code: 'KX7M2P9Q',
              guest_name: 'Ada Lovelace',
              status: 'confirmed',
              check_in_date: '2026-10-11',
              check_out_date: '2026-10-14',
              nights: 3,
              adults: 1,
              children: 0,
              room_type_id: 1,
              room_type: 'STD',
              room_type_name: 'Standard Queen',
              room_id: null,
              room_number: null,
              is_vip: false,
              has_notes: false,
              version: 1,
            },
          ],
        },
      ],
    })
    renderApp('/')

    const arrivals = await screen.findByRole('region', { name: /Arrivals today/ })
    expect(within(arrivals).getByRole('link', { name: 'Ada Lovelace' })).toHaveAttribute(
      'href',
      '/reservations/31',
    )
    expect(arrivals).toHaveTextContent('No room yet')
    expect(arrivals).toHaveTextContent('Late: was due Sun, 11 Oct 2026')
  })
})

describe('amounts typed by staff', () => {
  it.each([
    ['1299', 129_900],
    ['1299.5', 129_950],
    ['1299,50', 129_950],
    [' 0 ', 0],
    ['12.345', null],
    ['-5', null],
    ['abc', null],
  ])('%s -> %s', (text, minor) => {
    expect(parseAmount(text)).toBe(minor)
  })
})
