/**
 * Pure helpers for the booking and change flows: plain dates (the hotel's calendar, no
 * time zone arithmetic), validation messages, and request bodies.
 */
import type { ApiError } from '../api/client'
import { HOUSEKEEPING } from '../dashboard/status'
import type {
  DashboardRoom,
  GuestCreate,
  ReservationCreate,
  ReservationSource,
  ReservationUpdate,
  WalkInCreate,
} from '../api/types'
import { parseAmount } from '../lib/format'

/** "2026-10-12" + n days. Dates are parsed and printed in UTC, so the browser's zone
 * cannot move them. */
export function addDays(isoDate: string, days: number): string {
  const date = new Date(`${isoDate}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

export function nightsBetween(checkIn: string, checkOut: string): number {
  const ms = Date.parse(`${checkOut}T00:00:00Z`) - Date.parse(`${checkIn}T00:00:00Z`)
  return Number.isNaN(ms) ? 0 : Math.round(ms / 86_400_000)
}

export interface StayInput {
  checkIn: string
  checkOut: string
  adults: string
  children: string
}

export type StayErrors = Partial<Record<keyof StayInput, string>>

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

/** What the form can tell before asking the server (which checks the hotel's limits). */
export function validateStay(
  stay: StayInput,
  today: string,
  { checkInFixed = false }: { checkInFixed?: boolean } = {},
): StayErrors {
  const errors: StayErrors = {}
  if (!ISO_DATE.test(stay.checkIn)) errors.checkIn = 'Enter the arrival date.'
  else if (!checkInFixed && stay.checkIn < today) errors.checkIn = 'Arrival cannot be in the past.'
  if (!ISO_DATE.test(stay.checkOut)) errors.checkOut = 'Enter the departure date.'
  else if (ISO_DATE.test(stay.checkIn) && stay.checkOut <= stay.checkIn) {
    errors.checkOut = 'Departure must be at least one day after arrival.'
  } else if (checkInFixed && stay.checkOut < today) {
    errors.checkOut = 'Departure cannot be in the past.'
  }
  const adults = Number(stay.adults)
  if (!Number.isInteger(adults) || adults < 1 || adults > 12) {
    errors.adults = 'Enter 1 to 12 adults.'
  }
  const children = Number(stay.children)
  if (!Number.isInteger(children) || children < 0 || children > 12) {
    errors.children = 'Enter 0 to 12 children.'
  }
  return errors
}

export function hasErrors(errors: object): boolean {
  return Object.values(errors).some(Boolean)
}

/** Server refusals of the stay (422) belong next to the date fields. */
export function stayErrorFromServer(error: ApiError): StayErrors {
  switch (error.code) {
    case 'check_in_in_past':
    case 'beyond_booking_horizon':
      return { checkIn: error.message }
    case 'invalid_dates':
    case 'stay_too_long':
    case 'check_out_in_past':
      return { checkOut: error.message }
    case 'invalid_guests':
    case 'too_many_guests':
      return { adults: error.message }
    default:
      return {}
  }
}

/** Nights the server says are full (409 no_availability). */
export function unavailableNights(error: ApiError): string[] {
  const body = error.body
  if (typeof body === 'object' && body !== null && 'unavailable_nights' in body) {
    const nights = body.unavailable_nights
    if (Array.isArray(nights)) return nights.filter((n): n is string => typeof n === 'string')
  }
  return []
}

export interface NewGuestInput {
  first_name: string
  last_name: string
  email: string
  phone: string
}

export const EMPTY_GUEST: NewGuestInput = { first_name: '', last_name: '', email: '', phone: '' }

export type GuestErrors = Partial<Record<keyof NewGuestInput, string>>

export function validateNewGuest(guest: NewGuestInput): GuestErrors {
  const errors: GuestErrors = {}
  if (!guest.first_name.trim()) errors.first_name = "Enter the guest's first name."
  if (!guest.last_name.trim()) errors.last_name = "Enter the guest's last name."
  if (guest.email.trim() && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(guest.email.trim())) {
    errors.email = 'Enter a valid email address, like name@example.com.'
  }
  const digits = guest.phone.replace(/\D/g, '')
  if (guest.phone.trim() && (!/^\+?[0-9 ()./-]+$/.test(guest.phone.trim()) || digits.length < 5)) {
    errors.phone = 'Enter a phone number with at least 5 digits, like +46 70 123 45 67.'
  }
  return errors
}

export function guestBody(guest: NewGuestInput): GuestCreate {
  return {
    first_name: guest.first_name.trim(),
    last_name: guest.last_name.trim(),
    email: guest.email.trim() || null,
    phone: guest.phone.trim() || null,
  }
}

/** Who the booking is for: an existing guest (by id) or a new one. */
export type GuestChoice =
  | { kind: 'existing'; id: number; name: string; detail: string }
  | { kind: 'new'; guest: NewGuestInput }

export interface Details {
  source: ReservationSource
  notes: string
  isVip: boolean
  /** Price per night in major units as typed ("1299.50"), or "" for the base rate. */
  rate: string
}

export const EMPTY_DETAILS: Details = { source: 'phone', notes: '', isVip: false, rate: '' }

export function rateError(rate: string): string | undefined {
  return rate.trim() && parseAmount(rate) === null
    ? 'Enter the price per night as a number, like 1299 or 1299.50.'
    : undefined
}

function guestFields(choice: GuestChoice): Pick<ReservationCreate, 'guest_id' | 'guest'> {
  return choice.kind === 'existing' ? { guest_id: choice.id } : { guest: guestBody(choice.guest) }
}

export function reservationBody(
  stay: StayInput,
  roomTypeId: number,
  choice: GuestChoice,
  details: Details,
): ReservationCreate {
  return {
    ...guestFields(choice),
    room_type_id: roomTypeId,
    check_in_date: stay.checkIn,
    check_out_date: stay.checkOut,
    adults: Number(stay.adults),
    children: Number(stay.children),
    source: details.source,
    notes: details.notes.trim() || null,
    is_vip: details.isVip,
    nightly_rate_minor: details.rate.trim() ? parseAmount(details.rate) : null,
  }
}

export function walkInBody(
  stay: StayInput,
  room: { room_id: number; version: number },
  choice: GuestChoice,
  details: Details,
): WalkInCreate {
  return {
    ...guestFields(choice),
    room_id: room.room_id,
    room_version: room.version,
    check_out_date: stay.checkOut,
    adults: Number(stay.adults),
    children: Number(stay.children),
    notes: details.notes.trim() || null,
    is_vip: details.isVip,
    nightly_rate_minor: details.rate.trim() ? parseAmount(details.rate) : null,
  }
}

/** A key for one booking attempt: made when the form opens, reused for every retry. */
export function newIdempotencyKey(): string {
  return crypto.randomUUID().replaceAll('-', '')
}

/** Why a room cannot take a walk-in now, or null if it can (vacant, clean, not held). */
const READY = new Set(['clean', 'inspected'])

export function roomUnavailableReason(room: DashboardRoom): string | null {
  if (room.occupancy_status === 'occupied' || room.occupancy_status === 'departing_today') {
    return 'occupied'
  }
  if (room.occupancy_status === 'out_of_order') return 'out of order'
  if (room.reservation_id !== null) return 'held for an arrival'
  if (!READY.has(room.housekeeping_status)) {
    return HOUSEKEEPING[room.housekeeping_status].label.toLowerCase()
  }
  return null
}

export interface ChangeValues {
  stay: StayInput
  roomTypeId: number
  notes: string
  isVip: boolean
  rate: string
}

/** The PATCH body for what changed (with the version the form was loaded with), or
 * null if nothing changed. A checked-in stay only sends departure and notes. */
export function changeBody(
  r: {
    version: number
    status: string
    check_in_date: string
    check_out_date: string
    adults: number
    children: number
    room_type_id: number
    notes: string | null
    is_vip: boolean
  },
  v: ChangeValues,
): ReservationUpdate | null {
  const body: ReservationUpdate = { version: r.version }
  const inHouse = r.status === 'checked_in'
  if (v.stay.checkOut !== r.check_out_date) body.check_out_date = v.stay.checkOut
  const notes = v.notes.trim() || null
  if (notes !== r.notes) body.notes = notes
  if (!inHouse) {
    if (v.stay.checkIn !== r.check_in_date) body.check_in_date = v.stay.checkIn
    if (Number(v.stay.adults) !== r.adults) body.adults = Number(v.stay.adults)
    if (Number(v.stay.children) !== r.children) body.children = Number(v.stay.children)
    if (v.roomTypeId !== r.room_type_id) body.room_type_id = v.roomTypeId
    if (v.isVip !== r.is_vip) body.is_vip = v.isVip
  }
  if (v.rate.trim()) {
    const minor = parseAmount(v.rate)
    if (minor !== null) body.nightly_rate_minor = minor
  }
  return Object.keys(body).length > 1 ? body : null
}
