import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { Availability, GuestPage, ReservationDetail, RoomTypeAvailability } from '../api/types'
import {
  BUSINESS_DATE,
  dashboardHandlers,
  HOTEL,
  makeReservation,
  makeReservationView,
  makeRoom,
} from '../test/fixtures'
import {
  FRONT_DESK_USER,
  FRONT_OFFICE_MANAGER_USER,
  HOUSEKEEPING_SUPERVISOR_USER,
  mockApi,
  renderApp,
} from '../test/renderApp'
import { addDays, changeBody, nightsBetween, validateStay } from './booking'

// --- fixtures -----------------------------------------------------------------------------

const TOMORROW = addDays(BUSINESS_DATE, 1)
const IN_TWO = addDays(BUSINESS_DATE, 2)

function roomType(overrides: Partial<RoomTypeAvailability>): RoomTypeAvailability {
  return {
    room_type_id: 1,
    code: 'STD',
    name: 'Standard Queen',
    max_occupancy: 2,
    bed_setup: '1 queen',
    available: 2,
    nightly_rates: [
      { night_date: BUSINESS_DATE, rate_minor: 129_900 },
      { night_date: TOMORROW, rate_minor: 129_900 },
    ],
    total_minor: 259_800,
    currency: 'SEK',
    ...overrides,
  }
}

const STANDARD = roomType({})
const SUITE = roomType({
  room_type_id: 3,
  code: 'STE',
  name: 'Junior Suite',
  max_occupancy: 4,
  available: 0,
  total_minor: 499_800,
})
const DELUXE = roomType({
  room_type_id: 2,
  code: 'DLX',
  name: 'Deluxe King',
  max_occupancy: 3,
  available: 1,
  total_minor: 359_800,
})

function availability(types: RoomTypeAvailability[], checkOut = IN_TWO): Availability {
  return {
    check_in_date: BUSINESS_DATE,
    check_out_date: checkOut,
    nights: nightsBetween(BUSINESS_DATE, checkOut),
    adults: 1,
    children: 0,
    currency: 'SEK',
    room_types: types,
  }
}

const AVAILABILITY_URL = (checkOut: string, extra = '') =>
  `GET /api/reservations/availability?check_in=${BUSINESS_DATE}&check_out=${checkOut}&adults=1&children=0${extra}`

const NO_GUESTS: GuestPage = { items: [], total: 0, page: 1, page_size: 8 }

const BOOKED: ReservationDetail = makeReservation({
  id: 77,
  confirmation_code: 'KX7M2P9Q',
  status: 'confirmed',
  check_in_date: BUSINESS_DATE,
  check_out_date: IN_TWO,
  room_id: null,
  room_number: null,
  guest: {
    id: 5,
    first_name: 'Ada',
    last_name: 'Lovelace',
    email: null,
    phone: null,
    contact_hidden: false,
  },
})

type Handler = () => [number, unknown] | Promise<[number, unknown]>

function base(me = FRONT_DESK_USER): Record<string, Handler> {
  return {
    'GET /api/auth/me': () => [200, me],
    'GET /api/hotel': () => [200, HOTEL],
  }
}

function posts(fetchMock: ReturnType<typeof mockApi>, path: string) {
  return fetchMock.mock.calls.filter(([p, init]) => p === path && init?.method === 'POST')
}

function bodyOf(call: Parameters<typeof fetch> | undefined): unknown {
  const body = call?.[1]?.body
  return typeof body === 'string' ? JSON.parse(body) : undefined
}

function keyOf(call: Parameters<typeof fetch> | undefined): string | undefined {
  const headers = call?.[1]?.headers
  return headers && !Array.isArray(headers) && !(headers instanceof Headers)
    ? headers['Idempotency-Key']
    : undefined
}

async function heading(name: string) {
  return screen.findByRole('heading', { name, level: 2 })
}

/** Dates (departure in two days) -> Standard -> a new guest, Ada Lovelace -> review. */
async function fillUpToReview(user: ReturnType<typeof userEvent.setup>) {
  await heading('Dates and guests')
  const departure = screen.getByLabelText('Departure')
  await user.clear(departure)
  await user.type(departure, IN_TWO)
  expect(screen.getByText('2 nights')).toBeVisible()
  await user.click(screen.getByRole('button', { name: 'Continue' }))

  await heading('Room type')
  await user.click(await screen.findByRole('radio', { name: /Standard Queen/ }))
  await user.click(screen.getByRole('button', { name: 'Continue' }))

  await heading('Guest')
  await user.click(screen.getByRole('radio', { name: 'New guest' }))
  await user.type(screen.getByLabelText('First name'), 'Ada')
  await user.type(screen.getByLabelText('Last name'), 'Lovelace')
  await user.click(screen.getByRole('button', { name: 'Continue' }))

  await heading('Details')
  await user.selectOptions(screen.getByLabelText('Booked by'), 'email')
  await user.type(screen.getByLabelText(/Notes/), 'Late arrival')
  await user.click(screen.getByRole('button', { name: 'Continue' }))
  await heading('Review and confirm')
}

// --- helpers ------------------------------------------------------------------------------

describe('booking helpers', () => {
  it('counts nights in the hotel calendar', () => {
    expect(nightsBetween('2026-10-30', '2026-11-02')).toBe(3)
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01')
  })

  it('validates a stay before asking the server', () => {
    const stay = { checkIn: BUSINESS_DATE, checkOut: BUSINESS_DATE, adults: '0', children: '-1' }
    expect(validateStay(stay, BUSINESS_DATE)).toEqual({
      checkOut: 'Departure must be at least one day after arrival.',
      adults: 'Enter 1 to 12 adults.',
      children: 'Enter 0 to 12 children.',
    })
    expect(
      validateStay(
        { ...stay, checkIn: '2026-10-01', checkOut: IN_TWO, adults: '1', children: '0' },
        BUSINESS_DATE,
      ),
    ).toEqual({
      checkIn: 'Arrival cannot be in the past.',
    })
  })

  it('a change sends only what changed, and a stay in house only departure and notes', () => {
    const booking = makeReservationView({ status: 'checked_in', notes: null })
    const values = {
      stay: { checkIn: '2026-10-01', checkOut: '2026-10-20', adults: '3', children: '0' },
      roomTypeId: 9,
      notes: ' Extra towels ',
      isVip: true,
      rate: '',
    }
    expect(changeBody(booking, values)).toEqual({
      version: 1,
      check_out_date: '2026-10-20',
      notes: 'Extra towels',
    })
    expect(changeBody({ ...booking, status: 'confirmed' }, { ...values, notes: '' })).toEqual({
      version: 1,
      check_in_date: '2026-10-01',
      check_out_date: '2026-10-20',
      adults: 3,
      room_type_id: 9,
      is_vip: true,
    })
  })
})

// --- new reservation ----------------------------------------------------------------------

describe('new reservation', () => {
  it('books a new guest from dates to the confirmation code', async () => {
    const fetchMock = mockApi({
      ...base(),
      [AVAILABILITY_URL(IN_TWO)]: () => [200, availability([STANDARD, DELUXE, SUITE])],
      'GET /api/guests?q=Ada+Lovelace&page_size=8': () => [200, NO_GUESTS],
      'POST /api/reservations': () => [201, BOOKED],
    })
    renderApp('/reservations/new')
    const user = userEvent.setup()

    // Dates start at the hotel's today and tomorrow.
    await heading('Dates and guests')
    expect(screen.getByLabelText('Arrival')).toHaveValue(BUSINESS_DATE)
    expect(screen.getByLabelText('Departure')).toHaveValue(TOMORROW)
    expect(screen.getByText('1 night')).toBeVisible()
    await user.clear(screen.getByLabelText('Departure'))
    await user.type(screen.getByLabelText('Departure'), IN_TWO)
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    // Room types with what is free and what it costs; a full one cannot be chosen.
    await heading('Room type')
    const standard = await screen.findByRole('radio', { name: /Standard Queen/ })
    expect(standard.closest('label')).toHaveTextContent('2 rooms available')
    expect(standard.closest('label')).toHaveTextContent('SEK 1,299.00 per night')
    expect(standard.closest('label')).toHaveTextContent('SEK 2,598.00 total')
    const suite = screen.getByRole('radio', { name: /Junior Suite/ })
    expect(suite).toBeDisabled()
    expect(suite.closest('label')).toHaveTextContent('Full: no room of this type is free')
    // Continuing without a choice says why.
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    expect(screen.getByText('Choose a room type.')).toBeVisible()
    await user.click(standard)
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    // A new guest: names are required and errors are shown next to the fields.
    await heading('Guest')
    await user.click(screen.getByRole('radio', { name: 'New guest' }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    expect(screen.getByText("Enter the guest's first name.")).toBeVisible()
    expect(screen.getByLabelText('First name')).toHaveAttribute('aria-invalid', 'true')
    await user.type(screen.getByLabelText('First name'), 'Ada')
    await user.type(screen.getByLabelText('Last name'), 'Lovelace')
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    await heading('Details')
    // No rate override without reservation.override_rate.
    expect(screen.queryByLabelText(/Price per night/)).not.toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Booked by'), 'email')
    await user.click(screen.getByLabelText('VIP guest'))
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    const review = await heading('Review and confirm')
    expect(review).toHaveFocus()
    const summary = screen.getByRole('region', { name: 'Review and confirm' })
    expect(summary).toHaveTextContent('Mon, 12 Oct 2026 to Wed, 14 Oct 2026, 2 nights')
    expect(summary).toHaveTextContent('Standard Queen')
    expect(summary).toHaveTextContent('Ada Lovelace (new)')
    expect(summary).toHaveTextContent('Email')
    expect(summary).toHaveTextContent('SEK 2,598.00 total')
    await user.click(screen.getByRole('button', { name: 'Confirm booking' }))

    // Success: the code, large, and where to go next.
    const done = await screen.findByRole('heading', { name: 'Booking confirmed' })
    expect(done).toHaveFocus()
    expect(screen.getByLabelText('Confirmation code K X 7 M 2 P 9 Q')).toHaveTextContent(
      'KX7M 2P9Q',
    )
    expect(screen.getByRole('link', { name: 'Open the reservation' })).toHaveAttribute(
      'href',
      '/reservations/77',
    )
    expect(screen.getByRole('link', { name: 'New reservation' })).toHaveAttribute(
      'href',
      '/reservations/new',
    )
    const [call] = posts(fetchMock, '/api/reservations')
    expect(bodyOf(call)).toEqual({
      guest: { first_name: 'Ada', last_name: 'Lovelace', email: null, phone: null },
      room_type_id: 1,
      check_in_date: BUSINESS_DATE,
      check_out_date: IN_TWO,
      adults: 1,
      children: 0,
      source: 'email',
      notes: null,
      is_vip: true,
      nightly_rate_minor: null,
    })
    expect(keyOf(call)).toMatch(/^[0-9a-f]{32}$/)
  })

  it('finds a guest on file, and warns about a likely duplicate', async () => {
    const ada = {
      id: 5,
      first_name: 'Ada',
      last_name: 'Lovelace',
      email: 'ada@example.com',
      phone: null,
      contact_hidden: false,
      notes: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    }
    const found: GuestPage = { items: [ada], total: 1, page: 1, page_size: 8 }
    const fetchMock = mockApi({
      ...base(),
      [AVAILABILITY_URL(IN_TWO)]: () => [200, availability([STANDARD])],
      'GET /api/guests?q=Ada+Lovelace&page_size=8': () => [200, found],
      'GET /api/guests?q=lovel&page_size=8': () => [200, found],
      'POST /api/reservations': () => [201, BOOKED],
    })
    renderApp('/reservations/new')
    const user = userEvent.setup()

    await heading('Dates and guests')
    await user.clear(screen.getByLabelText('Departure'))
    await user.type(screen.getByLabelText('Departure'), IN_TWO)
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(await screen.findByRole('radio', { name: /Standard Queen/ }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    // Typing a new guest that is already on file shows a warning with a shortcut.
    await heading('Guest')
    await user.click(screen.getByRole('radio', { name: 'New guest' }))
    await user.type(screen.getByLabelText('First name'), 'Ada')
    await user.type(screen.getByLabelText('Last name'), 'Lovelace')
    expect(
      await screen.findByText('A similar guest is already on file. Is this them?'),
    ).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Use this guest: Ada Lovelace' }))
    expect(screen.getByText(/Booking for/)).toHaveTextContent('Ada Lovelace (ada@example.com)')

    // Search works too.
    await user.type(screen.getByRole('searchbox', { name: 'Find a guest' }), 'lovel')
    expect(await screen.findByText('1 guest found')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Choose Ada Lovelace', pressed: true }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await heading('Details')
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await heading('Review and confirm')
    await user.click(screen.getByRole('button', { name: 'Confirm booking' }))

    await screen.findByRole('heading', { name: 'Booking confirmed' })
    expect(bodyOf(posts(fetchMock, '/api/reservations')[0])).toMatchObject({ guest_id: 5 })
  })

  it('a double click sends one booking, and a retry reuses the same key', async () => {
    let attempts = 0
    let release: (() => void) | null = null
    const fetchMock = mockApi({
      ...base(),
      [AVAILABILITY_URL(IN_TWO)]: () => [200, availability([STANDARD])],
      'GET /api/guests?q=Ada+Lovelace&page_size=8': () => [200, NO_GUESTS],
      'POST /api/reservations': () => {
        attempts += 1
        // The first try fails like a lost connection; the second is held open, then succeeds.
        if (attempts === 1) return [503, { detail: 'Service unavailable' }]
        return new Promise((resolve) => {
          release = () => {
            resolve([201, BOOKED])
          }
        })
      },
    })
    renderApp('/reservations/new')
    const user = userEvent.setup()
    await fillUpToReview(user)

    await user.click(screen.getByRole('button', { name: 'Confirm booking' }))
    expect(await screen.findByText('Something went wrong. Confirm again.')).toBeVisible()

    const confirm = screen.getByRole('button', { name: 'Confirm booking' })
    await user.dblClick(confirm)
    await waitFor(() => {
      expect(release).not.toBeNull()
    })
    expect(screen.getByRole('button', { name: 'Booking…' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    await user.click(screen.getByRole('button', { name: 'Booking…' }))
    ;(release as unknown as () => void)()

    await screen.findByRole('heading', { name: 'Booking confirmed' })
    const calls = posts(fetchMock, '/api/reservations')
    // The failed try and one more: the double click and the extra click sent nothing.
    expect(calls).toHaveLength(2)
    expect(keyOf(calls[0])).toBe(keyOf(calls[1]))
  })

  it('keeps everything and refreshes availability when the room type filled up', async () => {
    let availabilityCalls = 0
    let bookingCalls = 0
    const fetchMock = mockApi({
      ...base(),
      [AVAILABILITY_URL(IN_TWO)]: () => {
        availabilityCalls += 1
        return [
          200,
          availability(
            availabilityCalls === 1 ? [STANDARD, DELUXE] : [{ ...STANDARD, available: 0 }, DELUXE],
          ),
        ]
      },
      'GET /api/guests?q=Ada+Lovelace&page_size=8': () => [200, NO_GUESTS],
      'POST /api/reservations': () => {
        bookingCalls += 1
        return bookingCalls === 1
          ? [
              409,
              {
                detail: 'No Standard Queen room is free on 2026-10-13.',
                code: 'no_availability',
                unavailable_nights: ['2026-10-13'],
              },
            ]
          : [201, BOOKED]
      },
    })
    renderApp('/reservations/new')
    const user = userEvent.setup()
    await fillUpToReview(user)

    await user.click(screen.getByRole('button', { name: 'Confirm booking' }))

    // Back at the room types, told what happened, with fresh counts.
    const step = await heading('Room type')
    expect(step).toHaveFocus()
    expect(
      await screen.findByText(
        /While you were booking, Standard Queen filled up on Tue, 13 Oct 2026\. Availability has been refreshed/,
      ),
    ).toBeVisible()
    const standard = await screen.findByRole('radio', { name: /Standard Queen/ })
    await waitFor(() => {
      expect(standard).toBeDisabled()
    })
    expect(availabilityCalls).toBe(2)
    await user.click(screen.getByRole('radio', { name: /Deluxe King/ }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    // The guest and details are still there.
    await heading('Guest')
    expect(screen.getByLabelText('First name')).toHaveValue('Ada')
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await heading('Details')
    expect(screen.getByLabelText(/Notes/)).toHaveValue('Late arrival')
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await heading('Review and confirm')
    await user.click(screen.getByRole('button', { name: 'Confirm booking' }))

    await screen.findByRole('heading', { name: 'Booking confirmed' })
    const calls = posts(fetchMock, '/api/reservations')
    expect(bodyOf(calls[1])).toMatchObject({ room_type_id: 2, notes: 'Late arrival' })
    // Same attempt, same key: the refused try stored nothing.
    expect(keyOf(calls[0])).toBe(keyOf(calls[1]))
  })

  it('shows date problems next to the fields and announces them', async () => {
    mockApi({ ...base() })
    renderApp('/reservations/new')
    const user = userEvent.setup()

    await heading('Dates and guests')
    await user.clear(screen.getByLabelText('Departure'))
    await user.type(screen.getByLabelText('Departure'), BUSINESS_DATE)
    await user.clear(screen.getByLabelText('Adults'))
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    expect(screen.getByText('Check the dates and guests.')).toBeVisible()
    expect(screen.getByText('Departure must be at least one day after arrival.')).toBeVisible()
    expect(screen.getByLabelText('Departure')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText('Enter 1 to 12 adults.')).toBeVisible()
    // Still on the first step.
    expect(screen.getByRole('heading', { name: 'Dates and guests', level: 2 })).toBeVisible()
  })

  it('front office managers may set the price per night', async () => {
    const fetchMock = mockApi({
      ...base(FRONT_OFFICE_MANAGER_USER),
      [AVAILABILITY_URL(TOMORROW)]: () => [200, availability([STANDARD], TOMORROW)],
      'GET /api/guests?q=Ada+Lovelace&page_size=8': () => [200, NO_GUESTS],
      'POST /api/reservations': () => [201, BOOKED],
    })
    renderApp('/reservations/new')
    const user = userEvent.setup()

    await heading('Dates and guests')
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(await screen.findByRole('radio', { name: /Standard Queen/ }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(await screen.findByRole('radio', { name: 'New guest' }))
    await user.type(screen.getByLabelText('First name'), 'Ada')
    await user.type(screen.getByLabelText('Last name'), 'Lovelace')
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await heading('Details')
    await user.type(screen.getByLabelText(/Price per night \(SEK\)/), '999.50')
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    expect(await screen.findByText(/SEK 999.50 per night \(set by you\)/)).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Confirm booking' }))

    await screen.findByRole('heading', { name: 'Booking confirmed' })
    expect(bodyOf(posts(fetchMock, '/api/reservations')[0])).toMatchObject({
      nightly_rate_minor: 99_950,
    })
  })
})

// --- walk-in ------------------------------------------------------------------------------

describe('walk-in', () => {
  it('the dashboard offers it with create and check-in permissions only', async () => {
    mockApi({ 'GET /api/auth/me': () => [200, FRONT_DESK_USER], ...dashboardHandlers() })
    renderApp('/')
    expect(await screen.findByRole('link', { name: 'Walk-in' })).toHaveAttribute(
      'href',
      '/reservations/walk-in',
    )
  })

  it('is not offered to roles that cannot book', async () => {
    mockApi({
      'GET /api/auth/me': () => [200, HOUSEKEEPING_SUPERVISOR_USER],
      ...dashboardHandlers(),
    })
    renderApp('/')
    await screen.findByRole('button', { name: /^Room 201,/ })
    expect(screen.queryByRole('link', { name: 'Walk-in' })).not.toBeInTheDocument()
  })

  it('books from today, picks a clean room and checks in', async () => {
    const rooms = [
      makeRoom({ room_id: 1, number: '101', housekeeping_status: 'inspected', version: 4 }),
      makeRoom({ room_id: 2, number: '102', housekeeping_status: 'dirty' }),
      makeRoom({ room_id: 3, number: '103', occupancy_status: 'occupied', guest_name: 'X' }),
      makeRoom({ room_id: 9, number: '201', room_type: 'DLX', housekeeping_status: 'clean' }),
    ]
    const fetchMock = mockApi({
      ...base(),
      [AVAILABILITY_URL(TOMORROW)]: () => [200, availability([STANDARD], TOMORROW)],
      'GET /api/guests?q=Ada+Lovelace&page_size=8': () => [200, NO_GUESTS],
      'GET /api/dashboard/rooms': () => [200, { business_date: BUSINESS_DATE, rooms }],
      'POST /api/reservations/walk-in': () => [
        201,
        { ...BOOKED, status: 'checked_in', source: 'walk_in', room_id: 1, room_number: '101' },
      ],
    })
    renderApp('/reservations/walk-in')
    const user = userEvent.setup()

    await heading('Dates and guests')
    expect(screen.getByLabelText('Arrival')).toBeDisabled()
    expect(screen.getByText(/The guest arrives today/)).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(await screen.findByRole('radio', { name: /Standard Queen/ }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await user.click(await screen.findByRole('radio', { name: 'New guest' }))
    await user.type(screen.getByLabelText('First name'), 'Ada')
    await user.type(screen.getByLabelText('Last name'), 'Lovelace')
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await heading('Details')
    expect(screen.queryByLabelText('Booked by')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    // Only Standard rooms; only clean, empty ones can be picked, and the others say why.
    await heading('Room')
    expect(screen.getByRole('group', { name: 'Room (1 ready room of 3)' })).toBeVisible()
    expect(screen.getByRole('radio', { name: /Room 102/ })).toBeDisabled()
    expect(screen.getByText('Not available: dirty')).toBeVisible()
    expect(screen.getByRole('radio', { name: /Room 103/ })).toBeDisabled()
    expect(screen.queryByRole('radio', { name: /Room 201/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: /Room 101/ }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    await heading('Review and confirm')
    await user.click(screen.getByRole('button', { name: 'Book and check in' }))

    expect(await screen.findByRole('heading', { name: 'Ada Lovelace is checked in' })).toBeVisible()
    const [call] = posts(fetchMock, '/api/reservations/walk-in')
    expect(bodyOf(call)).toEqual({
      guest: { first_name: 'Ada', last_name: 'Lovelace', email: null, phone: null },
      room_id: 1,
      room_version: 4,
      check_out_date: TOMORROW,
      adults: 1,
      children: 0,
      notes: null,
      is_vip: false,
      nightly_rate_minor: null,
    })
    expect(keyOf(call)).toMatch(/^[0-9a-f]{32}$/)
  })
})

// --- changing a booking -------------------------------------------------------------------

const BOOKING = makeReservationView({
  id: 31,
  confirmation_code: 'KX7M2P9Q',
  status: 'confirmed',
  check_in_date: BUSINESS_DATE,
  check_out_date: IN_TWO,
  room_id: null,
  room_number: null,
  nights: 2,
  nightly_rates: [
    { night_date: BUSINESS_DATE, rate_minor: 99_900 },
    { night_date: TOMORROW, rate_minor: 99_900 },
  ],
  total_minor: 199_800,
})

const EXTENDED = {
  ...BOOKING,
  check_out_date: addDays(BUSINESS_DATE, 3),
  nights: 3,
  nightly_rates: [...BOOKING.nightly_rates, { night_date: IN_TWO, rate_minor: 129_900 }],
  total_minor: 329_700,
  history: [],
}

describe('changing a booking', () => {
  it('shows the price difference before saving, then saves only what changed', async () => {
    let detailReads = 0
    const fetchMock = mockApi({
      ...base(),
      'GET /api/reservations/31': () => [
        200,
        ++detailReads === 1 ? BOOKING : { ...EXTENDED, version: 2 },
      ],
      [AVAILABILITY_URL(IN_TWO, '&exclude_reservation_id=31')]: () => [
        200,
        availability([STANDARD]),
      ],
      [AVAILABILITY_URL(addDays(BUSINESS_DATE, 3), '&exclude_reservation_id=31')]: () => [
        200,
        availability([STANDARD], addDays(BUSINESS_DATE, 3)),
      ],
      'POST /api/reservations/31/preview-change': () => [200, EXTENDED],
      'PATCH /api/reservations/31': () => [200, { ...EXTENDED, version: 2 }],
    })
    const router = renderApp('/reservations/31/edit')
    const user = userEvent.setup()

    await screen.findByRole('heading', { name: /Change Alan Turing's booking/ })
    const preview = screen.getByRole('complementary', { name: 'Price before and after' })
    expect(preview).toHaveTextContent('No changes yet. Total SEK 1,998.00.')
    await user.clear(screen.getByLabelText('Departure'))
    await user.type(screen.getByLabelText('Departure'), addDays(BUSINESS_DATE, 3))

    await waitFor(() => {
      expect(preview).toHaveTextContent('Up SEK 1,299.00')
    })
    expect(preview).toHaveTextContent('3 nights, SEK 3,297.00')
    expect(bodyOf(posts(fetchMock, '/api/reservations/31/preview-change').at(-1))).toEqual({
      version: 1,
      check_out_date: addDays(BUSINESS_DATE, 3),
    })
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/reservations/31')
    })
    expect(await screen.findByText('KX7M2P9Q has been changed.')).toBeVisible()
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')
    expect(bodyOf(patch)).toEqual({ version: 1, check_out_date: addDays(BUSINESS_DATE, 3) })
  })

  it('keeps the edits and explains when someone else changed the booking', async () => {
    let detailReads = 0
    const changedElsewhere = { ...BOOKING, version: 2, notes: 'Changed by a colleague' }
    let patches = 0
    const fetchMock = mockApi({
      ...base(),
      'GET /api/reservations/31': () => [200, ++detailReads === 1 ? BOOKING : changedElsewhere],
      [AVAILABILITY_URL(IN_TWO, '&exclude_reservation_id=31')]: () => [
        200,
        availability([STANDARD]),
      ],
      'POST /api/reservations/31/preview-change': () => [200, BOOKING],
      'PATCH /api/reservations/31': () =>
        ++patches === 1
          ? [409, { detail: 'Changed by someone else', code: 'version_conflict' }]
          : [200, { ...changedElsewhere, version: 3 }],
    })
    renderApp('/reservations/31/edit')
    const user = userEvent.setup()

    await screen.findByRole('heading', { name: /Change Alan Turing's booking/ })
    await user.click(screen.getByLabelText('VIP guest'))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    const alert = await screen.findByText(
      /Someone else changed this booking while you were editing it/,
    )
    expect(alert.closest('[role="alert"]')).not.toBeNull()
    expect(detailReads).toBe(2)
    expect(screen.getByLabelText('VIP guest')).toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => {
      expect(patches).toBe(2)
    })
    const calls = fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH')
    expect(bodyOf(calls[0])).toEqual({ version: 1, is_vip: true })
    // The second try carries the version just loaded.
    expect(bodyOf(calls[1])).toEqual({ version: 2, is_vip: true, notes: null })
  })

  it('a guest in house can only change the departure and notes', async () => {
    mockApi({
      ...base(),
      'GET /api/reservations/31': () => [
        200,
        { ...BOOKING, status: 'checked_in', check_in_date: '2026-10-10', room_number: '201' },
      ],
    })
    renderApp('/reservations/31/edit')

    await screen.findByText(
      'The guest is checked in: only the departure date and notes can change.',
    )
    expect(screen.getByLabelText('Arrival')).toBeDisabled()
    expect(screen.getByLabelText('Adults')).toBeDisabled()
    expect(screen.getByLabelText('Departure')).toBeEnabled()
    expect(screen.queryByRole('group', { name: 'Room type' })).not.toBeInTheDocument()
  })

  it('says so when a booking can no longer change', async () => {
    mockApi({
      ...base(),
      'GET /api/reservations/31': () => [200, { ...BOOKING, status: 'cancelled' }],
    })
    renderApp('/reservations/31/edit')

    expect(
      await screen.findByText(/This booking is cancelled and can no longer be changed/),
    ).toBeVisible()
  })

  it('the detail page links to it', async () => {
    mockApi({ ...base(), 'GET /api/reservations/31': () => [200, BOOKING] })
    renderApp('/reservations/31')

    const group = await screen.findByRole('group', { name: 'Actions' })
    expect(within(group).getByRole('link', { name: 'Change booking' })).toHaveAttribute(
      'href',
      '/reservations/31/edit',
    )
  })
})
