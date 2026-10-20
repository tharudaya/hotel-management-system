/** Building blocks shared by the booking flow, the walk-in flow and the change page. */
import { AlertTriangle, Check, Star } from 'lucide-react'
import { useEffect, useId, useState } from 'react'

import { ApiError } from '../api/client'
import type { Availability, DashboardRoom, GuestProfile, ReservationSource } from '../api/types'
import { Field } from '../admin/users/Field'
import { fieldClass, focusRing } from '../components/styles'
import { HOUSEKEEPING } from '../dashboard/status'
import { formatDate, formatMoney, plural } from '../lib/format'
import { SOURCE_LABEL } from '../reservations/status'
import {
  type Details,
  type GuestChoice,
  type GuestErrors,
  type NewGuestInput,
  nightsBetween,
  type StayErrors,
  roomUnavailableReason,
  type StayInput,
} from './booking'
import { useGuestSearch } from './queries'

// --- stay ----------------------------------------------------------------------------------

export function StayFields({
  value,
  errors,
  today,
  checkInFixed = false,
  guestsFixed = false,
  onChange,
}: {
  value: StayInput
  errors: StayErrors
  today: string
  /** The arrival date cannot change (walk-ins arrive today; guests in house are here). */
  checkInFixed?: boolean
  guestsFixed?: boolean
  onChange: (value: StayInput) => void
}) {
  const nights =
    value.checkIn && value.checkOut > value.checkIn
      ? nightsBetween(value.checkIn, value.checkOut)
      : 0
  function set(field: keyof StayInput, v: string) {
    onChange({ ...value, [field]: v })
  }
  return (
    <fieldset className="space-y-3">
      <legend className="sr-only">Dates and guests</legend>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          label="Arrival"
          type="date"
          required
          min={checkInFixed ? undefined : today}
          disabled={checkInFixed}
          hint={checkInFixed ? undefined : 'Hotel calendar date.'}
          value={value.checkIn}
          error={errors.checkIn}
          onChange={(e) => {
            set('checkIn', e.target.value)
          }}
        />
        <Field
          label="Departure"
          type="date"
          required
          min={value.checkIn || today}
          value={value.checkOut}
          error={errors.checkOut}
          onChange={(e) => {
            set('checkOut', e.target.value)
          }}
        />
        <Field
          label="Adults"
          type="number"
          required
          min={1}
          max={12}
          disabled={guestsFixed}
          value={value.adults}
          error={errors.adults}
          onChange={(e) => {
            set('adults', e.target.value)
          }}
        />
        <Field
          label="Children"
          type="number"
          required
          min={0}
          max={12}
          disabled={guestsFixed}
          value={value.children}
          error={errors.children}
          onChange={(e) => {
            set('children', e.target.value)
          }}
        />
      </div>
      <p aria-live="polite" className="text-sm font-medium text-slate-800">
        {nights > 0 ? plural(nights, 'night') : 'Choose the dates to see the nights.'}
      </p>
    </fieldset>
  )
}

// --- room type -----------------------------------------------------------------------------

export function RoomTypeChoice({
  availability,
  selected,
  onSelect,
  currentRoomTypeId,
  error,
}: {
  availability: { data: Availability | undefined; isPending: boolean; error: Error | null }
  selected: number | null
  onSelect: (roomTypeId: number) => void
  /** When changing a booking: its own type is always selectable. */
  currentRoomTypeId?: number
  error?: string | undefined
}) {
  const errorId = useId()
  if (availability.isPending) {
    return (
      <p role="status" className="text-sm text-slate-600">
        Checking availability…
      </p>
    )
  }
  if (availability.error) {
    return (
      <p role="alert" className="text-sm text-red-800">
        {availability.error instanceof ApiError && availability.error.status < 500
          ? availability.error.message
          : 'Availability could not be loaded. Try again.'}
      </p>
    )
  }
  const types = availability.data?.room_types ?? []
  if (types.length === 0) {
    return (
      <p role="status" className="text-sm text-slate-700">
        No room type fits {plural(availability.data?.adults ?? 1, 'adult')}
        {availability.data?.children
          ? ` and ${plural(availability.data.children, 'child', 'children')}`
          : ''}
        .
      </p>
    )
  }
  return (
    <fieldset aria-describedby={error ? errorId : undefined} className="space-y-2">
      <legend className="text-sm font-medium text-slate-700">Room type</legend>
      {error && (
        <p id={errorId} role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
      {types.map((t) => {
        const current = t.room_type_id === currentRoomTypeId
        const full = t.available < 1 && !current
        const perNight = t.nightly_rates[0]?.rate_minor ?? 0
        const varies = t.nightly_rates.some((n) => n.rate_minor !== perNight)
        return (
          <label
            key={t.room_type_id}
            className={`flex cursor-pointer items-start gap-3 rounded-md border p-3 has-checked:border-sky-700 has-checked:bg-sky-50 ${
              full
                ? 'cursor-not-allowed border-slate-200 bg-slate-50 text-slate-500'
                : 'border-slate-300 bg-white'
            } has-focus-visible:ring-2 has-focus-visible:ring-sky-600`}
          >
            <input
              type="radio"
              name="room-type"
              value={t.room_type_id}
              checked={selected === t.room_type_id}
              disabled={full}
              onChange={() => {
                onSelect(t.room_type_id)
              }}
              className="mt-1 size-4"
            />
            <span className="flex-1 space-y-0.5 text-sm">
              <span className="block font-medium text-slate-900">
                {t.name}
                {current && <span className="font-normal text-slate-600"> (booked now)</span>}
              </span>
              <span className="block text-slate-600">
                Sleeps {t.max_occupancy}
                {t.bed_setup ? `, ${t.bed_setup}` : ''}
              </span>
              {full ? (
                <span className="flex items-center gap-1 font-medium text-slate-700">
                  <AlertTriangle aria-hidden="true" className="size-4" />
                  Full: no room of this type is free on every night of the stay.
                </span>
              ) : (
                <span className="block text-slate-800">
                  {current && t.available < 1
                    ? 'Keeps its room'
                    : `${plural(t.available, 'room')} available`}
                  {' · '}
                  {varies ? 'from ' : ''}
                  {formatMoney(perNight, t.currency)} per night
                  {' · '}
                  <strong>{formatMoney(t.total_minor, t.currency)}</strong> total
                </span>
              )}
            </span>
          </label>
        )
      })}
    </fieldset>
  )
}

// --- guest ---------------------------------------------------------------------------------

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebounced(value)
    }, ms)
    return () => {
      clearTimeout(timer)
    }
  }, [value, ms])
  return debounced
}

function contactLine(guest: GuestProfile): string {
  if (guest.contact_hidden) return 'Contact details hidden'
  return [guest.email, guest.phone].filter(Boolean).join(' · ') || 'No contact details'
}

function existing(guest: GuestProfile): GuestChoice {
  return {
    kind: 'existing',
    id: guest.id,
    name: `${guest.first_name} ${guest.last_name}`,
    detail: contactLine(guest),
  }
}

export function GuestChooser({
  value,
  errors,
  canSearch,
  onChange,
}: {
  value: GuestChoice
  errors: GuestErrors & { choice?: string }
  /** guest.view: search existing guests (otherwise only a new guest can be entered). */
  canSearch: boolean
  onChange: (value: GuestChoice) => void
}) {
  const [mode, setMode] = useState<'existing' | 'new'>(
    canSearch && value.kind === 'existing' ? 'existing' : 'new',
  )
  const [q, setQ] = useState('')
  const debouncedQ = useDebounced(q, 300)
  const search = useGuestSearch(debouncedQ, canSearch && mode === 'existing')
  const searchId = useId()
  const choiceErrorId = useId()
  const newGuest = value.kind === 'new' ? value.guest : null

  // Similar guests already on file, while typing a new one (by name, and by email).
  const nameQuery = newGuest ? `${newGuest.first_name} ${newGuest.last_name}`.trim() : ''
  const debouncedName = useDebounced(nameQuery, 400)
  const debouncedEmail = useDebounced(newGuest?.email.trim() ?? '', 400)
  const byName = useGuestSearch(
    debouncedName,
    canSearch &&
      mode === 'new' &&
      Boolean(newGuest?.first_name.trim() && newGuest.last_name.trim()),
  )
  const byEmail = useGuestSearch(
    debouncedEmail,
    canSearch && mode === 'new' && debouncedEmail.includes('@'),
  )
  const similar = [...(byName.data?.items ?? []), ...(byEmail.data?.items ?? [])].filter(
    (g, i, all) => all.findIndex((x) => x.id === g.id) === i,
  )

  function setNew(field: keyof NewGuestInput, v: string) {
    const guest = newGuest ?? { first_name: '', last_name: '', email: '', phone: '' }
    onChange({ kind: 'new', guest: { ...guest, [field]: v } })
  }

  return (
    <div className="space-y-4">
      {canSearch && (
        <div role="radiogroup" aria-label="Guest" className="flex gap-2">
          {(['existing', 'new'] as const).map((m) => (
            <label
              key={m}
              className={`inline-flex cursor-pointer items-center gap-2 rounded-md border border-slate-300 px-3 py-2 text-sm has-checked:border-sky-700 has-checked:bg-sky-50 has-focus-visible:ring-2 has-focus-visible:ring-sky-600`}
            >
              <input
                type="radio"
                name="guest-mode"
                checked={mode === m}
                onChange={() => {
                  setMode(m)
                  if (m === 'new' && value.kind !== 'new') {
                    onChange({
                      kind: 'new',
                      guest: { first_name: '', last_name: '', email: '', phone: '' },
                    })
                  }
                }}
                className="size-4"
              />
              {m === 'existing' ? 'Guest on file' : 'New guest'}
            </label>
          ))}
        </div>
      )}

      {mode === 'existing' && canSearch ? (
        <div className="space-y-3">
          <div className="space-y-1">
            <label htmlFor={searchId} className="block text-sm font-medium text-slate-700">
              Find a guest
            </label>
            <input
              id={searchId}
              type="search"
              value={q}
              maxLength={100}
              placeholder="Name, email or phone"
              aria-describedby={errors.choice ? choiceErrorId : undefined}
              onChange={(e) => {
                setQ(e.target.value)
              }}
              className={fieldClass}
            />
          </div>
          {errors.choice && (
            <p id={choiceErrorId} role="alert" className="text-sm text-red-700">
              {errors.choice}
            </p>
          )}
          {value.kind === 'existing' && (
            <p className="flex items-center gap-2 rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
              <Check aria-hidden="true" className="size-4" />
              Booking for <strong>{value.name}</strong> ({value.detail})
            </p>
          )}
          <div role="status" className="text-sm text-slate-600">
            {debouncedQ.trim().length < 2
              ? 'Type at least two letters.'
              : search.isPending
                ? 'Searching…'
                : search.isError
                  ? 'The search failed. Try again.'
                  : search.data.items.length === 0
                    ? 'No guest found. Add a new guest instead.'
                    : `${plural(search.data.total, 'guest')} found`}
          </div>
          {search.data && search.data.items.length > 0 && (
            <ul className="divide-y divide-slate-100 rounded-md border border-slate-200 bg-white">
              {search.data.items.map((g) => (
                <li
                  key={g.id}
                  className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
                >
                  <span>
                    <span className="font-medium text-slate-900">
                      {g.first_name} {g.last_name}
                    </span>
                    <span className="block text-slate-600">{contactLine(g)}</span>
                  </span>
                  <button
                    type="button"
                    aria-pressed={value.kind === 'existing' && value.id === g.id}
                    aria-label={`Choose ${g.first_name} ${g.last_name}`}
                    onClick={() => {
                      onChange(existing(g))
                    }}
                    className={`rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 aria-pressed:border-sky-700 aria-pressed:bg-sky-700 aria-pressed:text-white ${focusRing}`}
                  >
                    {value.kind === 'existing' && value.id === g.id ? 'Chosen' : 'Choose'}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field
              label="First name"
              required
              autoComplete="off"
              value={newGuest?.first_name ?? ''}
              error={errors.first_name}
              onChange={(e) => {
                setNew('first_name', e.target.value)
              }}
            />
            <Field
              label="Last name"
              required
              autoComplete="off"
              value={newGuest?.last_name ?? ''}
              error={errors.last_name}
              onChange={(e) => {
                setNew('last_name', e.target.value)
              }}
            />
            <Field
              label="Email"
              type="email"
              autoComplete="off"
              value={newGuest?.email ?? ''}
              error={errors.email}
              onChange={(e) => {
                setNew('email', e.target.value)
              }}
            />
            <Field
              label="Phone"
              type="tel"
              autoComplete="off"
              value={newGuest?.phone ?? ''}
              error={errors.phone}
              onChange={(e) => {
                setNew('phone', e.target.value)
              }}
            />
          </div>
          {similar.length > 0 && (
            <div
              role="status"
              className="space-y-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950"
            >
              <p className="flex items-center gap-2 font-medium">
                <AlertTriangle aria-hidden="true" className="size-4" />
                {similar.length === 1
                  ? 'A similar guest is already on file. Is this them?'
                  : `${plural(similar.length, 'similar guest')} are already on file. Is it one of them?`}
              </p>
              <ul className="space-y-1">
                {similar.slice(0, 5).map((g) => (
                  <li key={g.id} className="flex flex-wrap items-center justify-between gap-2">
                    <span>
                      {g.first_name} {g.last_name} · {contactLine(g)}
                    </span>
                    <button
                      aria-label={`Use this guest: ${g.first_name} ${g.last_name}`}
                      type="button"
                      onClick={() => {
                        setMode('existing')
                        onChange(existing(g))
                      }}
                      className={`rounded-md border border-amber-400 bg-white px-2 py-1 font-medium hover:bg-amber-100 ${focusRing}`}
                    >
                      Use this guest
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// --- details -------------------------------------------------------------------------------

const SOURCES: ReservationSource[] = ['phone', 'email', 'other']

export function DetailsFields({
  value,
  rateError,
  canOverrideRate,
  sourceFixed = false,
  currency,
  onChange,
}: {
  value: Details
  rateError?: string | undefined
  canOverrideRate: boolean
  /** Walk-ins are always source walk_in. */
  sourceFixed?: boolean
  currency: string
  onChange: (value: Details) => void
}) {
  const sourceId = useId()
  const notesId = useId()
  const vipId = useId()
  return (
    <div className="space-y-3">
      {!sourceFixed && (
        <div className="space-y-1">
          <label htmlFor={sourceId} className="block text-sm font-medium text-slate-700">
            Booked by
          </label>
          <select
            id={sourceId}
            value={value.source}
            onChange={(e) => {
              const source = SOURCES.find((s) => s === e.target.value) ?? 'phone'
              onChange({ ...value, source })
            }}
            className={fieldClass}
          >
            {SOURCES.map((s) => (
              <option key={s} value={s}>
                {SOURCE_LABEL[s]}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="space-y-1">
        <label htmlFor={notesId} className="block text-sm font-medium text-slate-700">
          Notes <span className="font-normal text-slate-500">(optional)</span>
        </label>
        <textarea
          id={notesId}
          rows={3}
          maxLength={2000}
          value={value.notes}
          onChange={(e) => {
            onChange({ ...value, notes: e.target.value })
          }}
          className={fieldClass}
        />
      </div>
      <div className="flex items-center gap-2">
        <input
          id={vipId}
          type="checkbox"
          checked={value.isVip}
          onChange={(e) => {
            onChange({ ...value, isVip: e.target.checked })
          }}
          className="size-4 rounded border-slate-300"
        />
        <label htmlFor={vipId} className="inline-flex items-center gap-1 text-sm text-slate-800">
          <Star aria-hidden className="size-3.5" />
          VIP guest
        </label>
      </div>
      {canOverrideRate && (
        <Field
          label={`Price per night (${currency})`}
          inputMode="decimal"
          hint="Leave empty to use the room type's rate. Applies to every night."
          value={value.rate}
          error={rateError}
          onChange={(e) => {
            onChange({ ...value, rate: e.target.value })
          }}
        />
      )}
    </div>
  )
}

// --- room (walk-in) ------------------------------------------------------------------------

export function RoomChooser({
  rooms,
  selected,
  error,
  onSelect,
}: {
  rooms: DashboardRoom[]
  selected: number | null
  error?: string | undefined
  onSelect: (roomId: number) => void
}) {
  const errorId = useId()
  const ready = rooms.filter((r) => roomUnavailableReason(r) === null)
  return (
    <fieldset aria-describedby={error ? errorId : undefined} className="space-y-2">
      <legend className="text-sm font-medium text-slate-700">
        Room ({plural(ready.length, 'ready room')} of {rooms.length})
      </legend>
      {error && (
        <p id={errorId} role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
      {rooms.length === 0 && <p className="text-sm text-slate-700">No room of this type.</p>}
      <div className="grid gap-2 sm:grid-cols-3">
        {rooms.map((room) => {
          const reason = roomUnavailableReason(room)
          return (
            <label
              key={room.room_id}
              className={`flex items-center gap-2 rounded-md border p-2 text-sm has-checked:border-sky-700 has-checked:bg-sky-50 has-focus-visible:ring-2 has-focus-visible:ring-sky-600 ${
                reason
                  ? 'cursor-not-allowed border-slate-200 bg-slate-50 text-slate-500'
                  : 'cursor-pointer border-slate-300 bg-white'
              }`}
            >
              <input
                type="radio"
                name="room"
                value={room.room_id}
                checked={selected === room.room_id}
                disabled={reason !== null}
                onChange={() => {
                  onSelect(room.room_id)
                }}
                className="size-4"
              />
              <span>
                <span className="font-medium text-slate-900">Room {room.number}</span>
                <span className="block text-xs">
                  {reason
                    ? `Not available: ${reason}`
                    : HOUSEKEEPING[room.housekeeping_status].label}
                </span>
              </span>
            </label>
          )
        })}
      </div>
    </fieldset>
  )
}

export function StaySummary({ stay }: { stay: StayInput }) {
  const nights = nightsBetween(stay.checkIn, stay.checkOut)
  return (
    <>
      {formatDate(stay.checkIn)} to {formatDate(stay.checkOut)} ({plural(nights, 'night')})
    </>
  )
}
