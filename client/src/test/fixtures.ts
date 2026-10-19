import type {
  DashboardRoom,
  DashboardSummary,
  ReservationDetail,
  ReservationView,
} from '../api/types'

export const BUSINESS_DATE = '2026-10-12'

export function makeRoom(overrides: Partial<DashboardRoom> = {}): DashboardRoom {
  return {
    room_id: 1,
    number: '101',
    floor: 1,
    room_type: 'STD',
    room_type_name: 'Standard Queen',
    housekeeping_status: 'clean',
    occupancy_status: 'vacant',
    guest_name: null,
    reservation_id: null,
    nights_remaining: null,
    is_vip: false,
    has_notes: false,
    has_arrival_today: false,
    version: 1,
    ...overrides,
  }
}

/** One room per status across two floors. */
export const ROOMS: DashboardRoom[] = [
  makeRoom({ room_id: 1, number: '101', housekeeping_status: 'inspected' }),
  makeRoom({
    room_id: 2,
    number: '102',
    occupancy_status: 'occupied',
    housekeeping_status: 'dirty',
    guest_name: 'Ada Lovelace',
    reservation_id: 11,
    nights_remaining: 2,
    is_vip: true,
    has_notes: true,
  }),
  makeRoom({
    room_id: 3,
    number: '103',
    room_type: 'DLX',
    room_type_name: 'Deluxe King',
    occupancy_status: 'arriving_today',
    housekeeping_status: 'clean',
    guest_name: 'Grace Hopper',
    reservation_id: 12,
    nights_remaining: 3,
    has_arrival_today: true,
  }),
  makeRoom({
    room_id: 4,
    number: '201',
    floor: 2,
    occupancy_status: 'departing_today',
    housekeeping_status: 'dirty',
    guest_name: 'Alan Turing',
    reservation_id: 13,
    nights_remaining: 0,
    has_arrival_today: true,
  }),
  makeRoom({
    room_id: 5,
    number: '202',
    floor: 2,
    room_type: 'DLX',
    room_type_name: 'Deluxe King',
    occupancy_status: 'out_of_order',
    housekeeping_status: 'out_of_order',
  }),
  makeRoom({
    room_id: 6,
    number: '203',
    floor: 2,
    occupancy_status: 'arriving_today',
    housekeeping_status: 'dirty',
    guest_name: 'Katherine Johnson',
    reservation_id: 14,
    nights_remaining: 1,
    has_arrival_today: true,
  }),
]

export const SUMMARY: DashboardSummary = {
  business_date: BUSINESS_DATE,
  arrivals_today: 4,
  arrivals_checked_in: 1,
  departures_today: 2,
  departures_checked_out: 1,
  in_house: 2,
  in_house_guests: 3,
  available_tonight: 1,
  dirty: 3,
  out_of_order: 1,
  occupancy_percent: 80,
  arrivals: [],
}

export const HOTEL = {
  name: 'Grand Hotel Stockholm',
  timezone: 'Europe/Stockholm',
  business_date: BUSINESS_DATE,
}

export function makeReservation(overrides: Partial<ReservationDetail> = {}): ReservationDetail {
  return {
    id: 13,
    confirmation_code: 'TUR1NG42',
    status: 'checked_in',
    source: 'phone',
    check_in_date: '2026-10-10',
    check_out_date: BUSINESS_DATE,
    nights: 2,
    adults: 1,
    children: 0,
    is_vip: false,
    notes: null,
    room_type_id: 1,
    room_type: 'STD',
    room_type_name: 'Standard Queen',
    room_id: 4,
    room_number: '201',
    guest: {
      id: 7,
      first_name: 'Alan',
      last_name: 'Turing',
      email: 'alan@example.com',
      phone: null,
      contact_hidden: false,
    },
    nightly_rates: [
      { night_date: '2026-10-10', rate_minor: 129_900 },
      { night_date: '2026-10-11', rate_minor: 129_900 },
    ],
    total_minor: 259_800,
    currency: 'SEK',
    version: 1,
    created_at: '2026-10-01T10:00:00Z',
    ...overrides,
  }
}

/** A reservation as GET /api/reservations/{id} returns it (with its history). */
export function makeReservationView(overrides: Partial<ReservationView> = {}): ReservationView {
  return { ...makeReservation(), history: [], ...overrides }
}

/** A fixture room by id (throws if the fixture set changes underneath a test). */
export function fixtureRoom(roomId: number): DashboardRoom {
  const room = ROOMS.find((r) => r.room_id === roomId)
  if (!room) throw new Error(`No fixture room ${roomId}`)
  return room
}

/** Backend responses the dashboard page loads, for tests that just need it to render. */
export function dashboardHandlers(): Record<string, () => [number, unknown]> {
  return {
    'GET /api/hotel': () => [200, HOTEL],
    'GET /api/dashboard/summary': () => [200, SUMMARY],
    'GET /api/dashboard/rooms': () => [200, { business_date: BUSINESS_DATE, rooms: ROOMS }],
  }
}
