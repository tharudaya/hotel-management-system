/**
 * The booking flow, step by step, for a receptionist on the phone (and, with `walkIn`,
 * for a guest at the desk: from today, with a room to check in to).
 *
 * One Idempotency-Key is made when the flow opens and sent with every confirm attempt,
 * so a double click or a retry after a lost answer returns the same booking. If the room
 * type filled up in the meantime (409), everything entered is kept, availability is
 * refreshed and the flow goes back to the room types with an explanation.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check } from 'lucide-react'
import {
  type ReactNode,
  type RefObject,
  type SubmitEvent,
  useEffect,
  useRef,
  useState,
} from 'react'
import { Link } from 'react-router'

import { ApiError, apiFetch } from '../api/client'
import type { DashboardRooms, ReservationDetail } from '../api/types'
import { P } from '../auth/permissions'
import { usePermissions } from '../auth/usePermissions'
import { FormAlert } from '../components/AuthShell'
import { buttonPrimary, buttonSecondary, focusRing } from '../components/styles'
import { dashboardKeys, useHotelInfo } from '../dashboard/queries'
import { formatDate, formatMoney, parseAmount, plural } from '../lib/format'
import { SOURCE_LABEL } from '../reservations/status'
import {
  addDays,
  type Details,
  EMPTY_DETAILS,
  EMPTY_GUEST,
  type GuestChoice,
  type GuestErrors,
  hasErrors,
  newIdempotencyKey,
  nightsBetween,
  rateError,
  reservationBody,
  roomUnavailableReason,
  type StayErrors,
  type StayInput,
  stayErrorFromServer,
  unavailableNights,
  validateNewGuest,
  validateStay,
  walkInBody,
} from './booking'
import { DetailsFields, GuestChooser, RoomChooser, RoomTypeChoice, StayFields } from './fields'
import { useAvailability, useCreateReservation, useWalkIn } from './queries'

type StepId = 'stay' | 'room-type' | 'guest' | 'details' | 'room' | 'review'

const STEP_LABEL: Record<StepId, string> = {
  stay: 'Dates and guests',
  'room-type': 'Room type',
  guest: 'Guest',
  details: 'Details',
  room: 'Room',
  review: 'Review and confirm',
}

export function BookingFlow({ walkIn = false }: { walkIn?: boolean }) {
  const today = useHotelInfo().data?.business_date
  if (!today) {
    return (
      <p role="status" className="text-slate-600">
        Loading…
      </p>
    )
  }
  return <Flow walkIn={walkIn} today={today} />
}

function Flow({ walkIn, today }: { walkIn: boolean; today: string }) {
  const { can } = usePermissions()
  const queryClient = useQueryClient()
  const steps: StepId[] = walkIn
    ? ['stay', 'room-type', 'guest', 'details', 'room', 'review']
    : ['stay', 'room-type', 'guest', 'details', 'review']
  const [stepIndex, setStepIndex] = useState(0)
  const step = steps[stepIndex] ?? 'stay'
  // One key for this booking attempt, whatever happens until it succeeds.
  const [idempotencyKey] = useState(newIdempotencyKey)

  const [stay, setStay] = useState<StayInput>({
    checkIn: today,
    checkOut: addDays(today, 1),
    adults: '1',
    children: '0',
  })
  const [stayErrors, setStayErrors] = useState<StayErrors>({})
  const [roomTypeId, setRoomTypeId] = useState<number | null>(null)
  const [roomTypeError, setRoomTypeError] = useState<string>()
  const [guest, setGuest] = useState<GuestChoice>({ kind: 'new', guest: EMPTY_GUEST })
  const [guestErrors, setGuestErrors] = useState<GuestErrors & { choice?: string }>({})
  const [details, setDetails] = useState<Details>(
    walkIn ? { ...EMPTY_DETAILS, source: 'walk_in' } : EMPTY_DETAILS,
  )
  const [detailsError, setDetailsError] = useState<string>()
  const [roomId, setRoomId] = useState<number | null>(null)
  const [roomError, setRoomError] = useState<string>()
  // A message for the whole step (announced), e.g. after a refused confirm.
  const [notice, setNotice] = useState<string | null>(null)
  const [booked, setBooked] = useState<ReservationDetail | null>(null)

  const stayValid = !hasErrors(validateStay(stay, today, { checkInFixed: walkIn }))
  const availability = useAvailability(
    stepIndex >= 1 && stayValid
      ? {
          checkIn: stay.checkIn,
          checkOut: stay.checkOut,
          adults: Number(stay.adults),
          children: Number(stay.children),
        }
      : null,
  )
  const roomType = availability.data?.room_types.find((t) => t.room_type_id === roomTypeId) ?? null
  const rooms = useQuery({
    queryKey: dashboardKeys.rooms,
    queryFn: ({ signal }) => apiFetch<DashboardRooms>('/api/dashboard/rooms', { signal }),
    enabled: walkIn && (step === 'room' || step === 'review'),
  })
  const roomsOfType = (rooms.data?.rooms ?? []).filter((r) => r.room_type === roomType?.code)
  const room = roomsOfType.find((r) => r.room_id === roomId) ?? null

  const create = useCreateReservation(idempotencyKey)
  const walk = useWalkIn(idempotencyKey)
  const pending = create.isPending || walk.isPending

  // Move focus to the new step's heading (not on first render).
  const headingRef = useRef<HTMLHeadingElement>(null)
  const firstRender = useRef(true)
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false
      return
    }
    headingRef.current?.focus()
  }, [stepIndex, booked])

  function goTo(target: StepId, message: string | null = null) {
    setNotice(message)
    setStepIndex(Math.max(0, steps.indexOf(target)))
  }

  function next(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    setNotice(null)
    if (step === 'stay') {
      const errors = validateStay(stay, today, { checkInFixed: walkIn })
      setStayErrors(errors)
      if (hasErrors(errors)) {
        setNotice('Check the dates and guests.')
        return
      }
    } else if (step === 'room-type') {
      if (availability.error instanceof ApiError && availability.error.status === 422) {
        setStayErrors(stayErrorFromServer(availability.error))
        goTo('stay', availability.error.message)
        return
      }
      if (!roomType) {
        setRoomTypeError('Choose a room type.')
        return
      }
      if (roomType.available < 1) {
        setRoomTypeError(`${roomType.name} is full for these dates. Choose another room type.`)
        return
      }
      setRoomTypeError(undefined)
    } else if (step === 'guest') {
      const errors: GuestErrors & { choice?: string } =
        guest.kind === 'new' ? validateNewGuest(guest.guest) : {}
      setGuestErrors(errors)
      if (hasErrors(errors)) {
        setNotice('Check the guest details.')
        return
      }
    } else if (step === 'details') {
      const error = rateError(details.rate)
      setDetailsError(error)
      if (error) return
    } else if (step === 'room') {
      if (!room || roomUnavailableReason(room) !== null) {
        setRoomError('Choose a clean, empty room.')
        return
      }
      setRoomError(undefined)
    }
    setStepIndex((i) => Math.min(i + 1, steps.length - 1))
  }

  function back() {
    setNotice(null)
    setStepIndex((i) => Math.max(i - 1, 0))
  }

  function refused(error: Error) {
    if (!(error instanceof ApiError)) {
      setNotice('The booking could not be sent. Check the connection and confirm again.')
      return
    }
    if (error.status === 409 && error.code === 'no_availability') {
      void queryClient.invalidateQueries({ queryKey: ['reservations', 'availability'] })
      const nights = unavailableNights(error).map(formatDate).join(', ')
      setRoomTypeError(undefined)
      goTo(
        'room-type',
        `While you were booking, ${roomType?.name ?? 'this room type'} filled up${nights ? ` on ${nights}` : ''}. Availability has been refreshed: choose another room type or change the dates. Everything else you entered is kept.`,
      )
      return
    }
    if (
      walkIn &&
      error.status === 409 &&
      ['room_not_vacant', 'room_not_ready', 'version_conflict', 'room_type_mismatch'].includes(
        error.code ?? '',
      )
    ) {
      void queryClient.invalidateQueries({ queryKey: dashboardKeys.rooms })
      setRoomId(null)
      goTo('room', `${error.message} The rooms have been refreshed: choose another room.`)
      return
    }
    if (error.status === 422 && hasErrors(stayErrorFromServer(error))) {
      setStayErrors(stayErrorFromServer(error))
      goTo('stay', error.message)
      return
    }
    if (error.code === 'guest_not_found') {
      setGuest({ kind: 'new', guest: EMPTY_GUEST })
      goTo('guest', 'That guest no longer exists. Find them again or add a new guest.')
      return
    }
    if (error.code === 'rate_override_not_allowed' || error.code === 'rate_not_set') {
      goTo('details', error.message)
      return
    }
    setNotice(error.status < 500 ? error.message : 'Something went wrong. Confirm again.')
  }

  function confirm() {
    if (!roomType || pending) return
    setNotice(null)
    if (walkIn) {
      if (!room) return
      walk.mutate(walkInBody(stay, room, guest, details), {
        onSuccess: setBooked,
        onError: refused,
      })
    } else {
      create.mutate(reservationBody(stay, roomType.room_type_id, guest, details), {
        onSuccess: setBooked,
        onError: refused,
      })
    }
  }

  if (booked) {
    return <Success reservation={booked} walkIn={walkIn} headingRef={headingRef} />
  }

  const override = details.rate.trim() ? parseAmount(details.rate) : null
  const nights = nightsBetween(stay.checkIn, stay.checkOut)
  const currency = availability.data?.currency ?? 'SEK'

  return (
    <div className="space-y-4">
      <Steps steps={steps} current={stepIndex} />
      <section
        aria-labelledby="step-heading"
        className="space-y-4 rounded-lg bg-white p-6 shadow-sm"
      >
        <h2
          id="step-heading"
          ref={headingRef}
          tabIndex={-1}
          className="text-lg font-semibold text-slate-900 focus:outline-none"
        >
          {STEP_LABEL[step]}
        </h2>
        <FormAlert message={notice} />
        <form onSubmit={next} noValidate className="space-y-4">
          {step === 'stay' && (
            <StayFieldsStep
              stay={stay}
              errors={stayErrors}
              today={today}
              walkIn={walkIn}
              onChange={(value) => {
                setStay(value)
                setStayErrors({})
              }}
            />
          )}
          {step === 'room-type' && (
            <RoomTypeChoice
              availability={availability}
              selected={roomTypeId}
              error={roomTypeError}
              onSelect={(id) => {
                setRoomTypeId(id)
                setRoomTypeError(undefined)
                setRoomId(null)
              }}
            />
          )}
          {step === 'guest' && (
            <GuestChooser
              value={guest}
              errors={guestErrors}
              canSearch={can(P.GUEST_VIEW)}
              onChange={(value) => {
                setGuest(value)
                setGuestErrors({})
              }}
            />
          )}
          {step === 'details' && (
            <DetailsFields
              value={details}
              rateError={detailsError}
              canOverrideRate={can(P.RESERVATION_OVERRIDE_RATE)}
              sourceFixed={walkIn}
              currency={currency}
              onChange={(value) => {
                setDetails(value)
                setDetailsError(undefined)
              }}
            />
          )}
          {step === 'room' &&
            (rooms.isPending ? (
              <p role="status" className="text-sm text-slate-600">
                Loading rooms…
              </p>
            ) : (
              <RoomChooser
                rooms={roomsOfType}
                selected={roomId}
                error={roomError}
                onSelect={(id) => {
                  setRoomId(id)
                  setRoomError(undefined)
                }}
              />
            ))}
          {step === 'review' && roomType && (
            <Review
              rows={[
                [
                  'Stay',
                  `${formatDate(stay.checkIn)} to ${formatDate(stay.checkOut)}, ${plural(nights, 'night')}`,
                ],
                [
                  'Guests',
                  [
                    plural(Number(stay.adults), 'adult'),
                    ...(Number(stay.children) > 0
                      ? [plural(Number(stay.children), 'child', 'children')]
                      : []),
                  ].join(', '),
                ],
                ['Room type', roomType.name],
                ...(walkIn && room ? [['Room', `Room ${room.number}`] as [string, ReactNode]] : []),
                [
                  'Guest',
                  guest.kind === 'existing'
                    ? `${guest.name} (on file)`
                    : `${guest.guest.first_name} ${guest.guest.last_name} (new)`,
                ],
                ['Booked by', SOURCE_LABEL[details.source]],
                ['VIP', details.isVip ? 'Yes' : 'No'],
                ['Notes', details.notes.trim() || 'None'],
                [
                  'Price',
                  override !== null
                    ? `${formatMoney(override, currency)} per night (set by you), ${formatMoney(override * nights, currency)} total`
                    : `${formatMoney(roomType.total_minor, currency)} total at the room type's rate`,
                ],
              ]}
            />
          )}

          <div className="flex flex-wrap justify-between gap-3 border-t border-slate-200 pt-4">
            <button
              type="button"
              onClick={back}
              disabled={stepIndex === 0 || pending}
              className={buttonSecondary}
            >
              Back
            </button>
            {step === 'review' ? (
              <button
                type="button"
                onClick={confirm}
                // aria-disabled, not disabled: keeps focus on the button while sending.
                aria-disabled={pending || undefined}
                className={buttonPrimary}
              >
                {pending ? 'Booking…' : walkIn ? 'Book and check in' : 'Confirm booking'}
              </button>
            ) : (
              <button type="submit" className={buttonPrimary}>
                Continue
              </button>
            )}
          </div>
        </form>
      </section>
    </div>
  )
}

function StayFieldsStep({
  stay,
  errors,
  today,
  walkIn,
  onChange,
}: {
  stay: StayInput
  errors: StayErrors
  today: string
  walkIn: boolean
  onChange: (value: StayInput) => void
}) {
  return (
    <>
      {walkIn && (
        <p className="text-sm text-slate-700">The guest arrives today, {formatDate(today)}.</p>
      )}
      <StayFields
        value={stay}
        errors={errors}
        today={today}
        checkInFixed={walkIn}
        onChange={onChange}
      />
    </>
  )
}

function Steps({ steps, current }: { steps: StepId[]; current: number }) {
  return (
    <ol aria-label="Booking steps" className="flex flex-wrap gap-2 text-sm">
      {steps.map((s, i) => (
        <li
          key={s}
          aria-current={i === current ? 'step' : undefined}
          className={`inline-flex items-center gap-1 rounded-full px-3 py-1 ${
            i === current
              ? 'bg-sky-700 font-medium text-white'
              : i < current
                ? 'bg-emerald-50 text-emerald-900'
                : 'bg-slate-100 text-slate-600'
          }`}
        >
          {i < current && <Check aria-hidden="true" className="size-3.5" />}
          <span>
            {i + 1}. {STEP_LABEL[s]}
          </span>
          {i < current && <span className="sr-only">(done)</span>}
        </li>
      ))}
    </ol>
  )
}

function Review({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-slate-500">{label}</dt>
          <dd className="text-slate-900">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

function Success({
  reservation: r,
  walkIn,
  headingRef,
}: {
  reservation: ReservationDetail
  walkIn: boolean
  headingRef: RefObject<HTMLHeadingElement | null>
}) {
  const spelled = r.confirmation_code.split('').join(' ')
  return (
    <section
      aria-labelledby="success-heading"
      className="space-y-5 rounded-lg bg-white p-8 text-center shadow-sm"
    >
      <h2
        id="success-heading"
        ref={headingRef}
        tabIndex={-1}
        className="text-xl font-semibold text-slate-900 focus:outline-none"
      >
        {walkIn ? `${r.guest.first_name} ${r.guest.last_name} is checked in` : 'Booking confirmed'}
      </h2>
      <div>
        <p className="text-sm text-slate-600">Confirmation code</p>
        <p
          aria-label={`Confirmation code ${spelled}`}
          className="font-mono text-5xl font-bold tracking-[0.25em] text-slate-900"
        >
          {r.confirmation_code.slice(0, 4)}
          <span aria-hidden="true"> </span>
          {r.confirmation_code.slice(4)}
        </p>
        <p className="mt-2 text-sm text-slate-600">
          For {r.guest.first_name} {r.guest.last_name}, {formatDate(r.check_in_date)} to{' '}
          {formatDate(r.check_out_date)}
          {r.room_number ? `, room ${r.room_number}` : ''}. {formatMoney(r.total_minor, r.currency)}{' '}
          total.
        </p>
      </div>
      <div className="flex flex-wrap justify-center gap-3">
        <Link to={`/reservations/${r.id}`} className={buttonPrimary}>
          Open the reservation
        </Link>
        <Link
          to={walkIn ? '/reservations/walk-in' : '/reservations/new'}
          className={`${buttonSecondary} ${focusRing}`}
        >
          {walkIn ? 'Another walk-in' : 'New reservation'}
        </Link>
      </div>
    </section>
  )
}
