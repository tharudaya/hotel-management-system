/**
 * What staff can do with a reservation, each shown only with the permission and status
 * that allow it (the server checks again). A version conflict closes the dialog, the
 * booking is refetched and an alert says someone else changed it.
 */
import { useQuery } from '@tanstack/react-query'
import { type ReactNode, type SubmitEvent, useId, useRef, useState } from 'react'

import { ApiError, apiFetch } from '../api/client'
import type {
  Availability,
  DashboardRoom,
  DashboardRooms,
  ReservationUpdate,
  ReservationView,
} from '../api/types'
import { P } from '../auth/permissions'
import { usePermissions } from '../auth/usePermissions'
import { FormAlert } from '../components/AuthShell'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { Dialog } from '../components/Dialog'
import { buttonPrimary, buttonSecondary, fieldClass } from '../components/styles'
import { Field } from '../admin/users/Field'
import { dashboardKeys, useHotelInfo, useRoomAction } from '../dashboard/queries'
import { HOUSEKEEPING, OCCUPANCY } from '../dashboard/status'
import { formatDate, formatMoney, parseAmount, plural } from '../lib/format'
import { changeErrorMessage, type ReservationChange, useReservationChange } from './queries'

type DialogKind = 'modify' | 'assign' | 'unassign' | 'check-in' | 'check-out' | 'cancel' | 'no-show'

interface Notice {
  kind: 'success' | 'conflict'
  text: string
}

const READY = new Set(['clean', 'inspected'])

function isConflict(error: Error): boolean {
  return error instanceof ApiError && error.status === 409 && error.code === 'version_conflict'
}

export function ReservationActions({ reservation: r }: { reservation: ReservationView }) {
  const { can } = usePermissions()
  const today = useHotelInfo().data?.business_date ?? null
  const [open, setOpen] = useState<DialogKind | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const guest = `${r.guest.first_name} ${r.guest.last_name}`
  const who = `${guest} (${r.confirmation_code})`

  const confirmed = r.status === 'confirmed'
  const inHouse = r.status === 'checked_in'
  // Picking a room needs the dashboard's room list (and its room versions).
  const seesRooms = can(P.DASHBOARD_VIEW)
  const allowed: Record<DialogKind, boolean> = {
    modify: can(P.RESERVATION_MODIFY) && (confirmed || inHouse),
    assign:
      can(P.RESERVATION_CHECK_IN) &&
      seesRooms &&
      confirmed &&
      today !== null &&
      r.check_out_date > today,
    unassign: can(P.RESERVATION_CHECK_IN) && confirmed && r.room_id !== null,
    'check-in':
      can(P.RESERVATION_CHECK_IN) &&
      seesRooms &&
      confirmed &&
      today !== null &&
      r.check_in_date <= today &&
      today < r.check_out_date,
    'check-out': can(P.RESERVATION_CHECK_OUT) && seesRooms && inHouse,
    cancel: can(P.RESERVATION_CANCEL) && confirmed,
    'no-show': can(P.RESERVATION_CANCEL) && confirmed && today !== null && r.check_in_date <= today,
  }
  const rooms = useStayRooms(allowed.assign || allowed['check-in'] || allowed['check-out'])

  function done(text: string) {
    setOpen(null)
    setNotice({ kind: 'success', text })
  }
  /** Returns true if the error was a conflict (and has been dealt with). */
  function conflict(error: Error): boolean {
    if (!isConflict(error)) return false
    setOpen(null)
    setNotice({ kind: 'conflict', text: changeErrorMessage(error) })
    return true
  }

  const buttons: { kind: DialogKind; label: string }[] = [
    { kind: 'check-in', label: 'Check in' },
    { kind: 'check-out', label: 'Check out' },
    { kind: 'assign', label: r.room_id === null ? 'Assign room' : 'Change room' },
    { kind: 'unassign', label: 'Unassign room' },
    { kind: 'modify', label: 'Change booking' },
    { kind: 'no-show', label: 'Mark no-show' },
    { kind: 'cancel', label: 'Cancel booking' },
  ]
  const visible = buttons.filter((b) => allowed[b.kind])

  return (
    <div className="space-y-3">
      {notice && (
        <div
          role={notice.kind === 'conflict' ? 'alert' : 'status'}
          className={
            notice.kind === 'conflict'
              ? 'rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900'
              : 'rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900'
          }
        >
          {notice.text}
        </div>
      )}
      {visible.length > 0 && (
        <div role="group" aria-label="Actions" className="flex flex-wrap gap-2">
          {visible.map((b) => (
            <button
              key={b.kind}
              type="button"
              onClick={() => {
                setNotice(null)
                setOpen(b.kind)
              }}
              className={
                b.kind === 'check-in' || b.kind === 'check-out' ? buttonPrimary : buttonSecondary
              }
            >
              {b.label}
            </button>
          ))}
        </div>
      )}

      {open === 'modify' && (
        <ModifyDialog
          reservation={r}
          onDone={() => {
            done(`${who} has been changed.`)
          }}
          onConflict={conflict}
          onClose={() => {
            setOpen(null)
          }}
        />
      )}
      {open === 'assign' && (
        <AssignRoomDialog
          reservation={r}
          rooms={rooms.data?.rooms ?? null}
          onDone={(number) => {
            done(`Room ${number} is now assigned to ${who}.`)
          }}
          onConflict={conflict}
          onClose={() => {
            setOpen(null)
          }}
        />
      )}
      {open === 'unassign' && (
        <SimpleChange
          reservation={r}
          title={`Unassign room ${r.room_number ?? ''}?`}
          description={
            <>
              {who} keeps the booking but no longer has room {r.room_number} set aside.
            </>
          }
          confirmLabel="Unassign room"
          change={{ kind: 'unassign-room', version: r.version }}
          onDone={() => {
            done(`${who} no longer has a room assigned.`)
          }}
          onConflict={conflict}
          onClose={() => {
            setOpen(null)
          }}
        />
      )}
      {open === 'no-show' && (
        <SimpleChange
          reservation={r}
          danger
          title={`Mark ${guest} as a no-show?`}
          description={
            <>
              Booking <strong>{r.confirmation_code}</strong> for {guest}, arriving{' '}
              {formatDate(r.check_in_date)}, will be closed as a no-show and its nights released.
              This cannot be undone.
            </>
          }
          confirmLabel="Mark no-show"
          change={{ kind: 'no-show', version: r.version }}
          onDone={() => {
            done(`${who} has been marked as a no-show.`)
          }}
          onConflict={conflict}
          onClose={() => {
            setOpen(null)
          }}
        />
      )}
      {open === 'cancel' && (
        <CancelDialog
          reservation={r}
          onDone={() => {
            done(`${who} has been cancelled.`)
          }}
          onConflict={conflict}
          onClose={() => {
            setOpen(null)
          }}
        />
      )}
      {open === 'check-in' && (
        <CheckInDialog
          reservation={r}
          rooms={rooms.data?.rooms ?? null}
          onDone={(number) => {
            done(`${guest} is checked in to room ${number}.`)
          }}
          onConflict={conflict}
          onClose={() => {
            setOpen(null)
          }}
        />
      )}
      {open === 'check-out' && (
        <CheckOutDialog
          reservation={r}
          today={today}
          room={rooms.data?.rooms.find((room) => room.room_id === r.room_id) ?? null}
          onDone={() => {
            done(`${guest} is checked out; room ${r.room_number ?? ''} is now dirty.`)
          }}
          onConflict={conflict}
          onClose={() => {
            setOpen(null)
          }}
        />
      )}
    </div>
  )
}

/** The dashboard's rooms (same cache), loaded only when a room has to be picked. */
function useStayRooms(enabled: boolean) {
  return useQuery({
    queryKey: dashboardKeys.rooms,
    queryFn: ({ signal }) => apiFetch<DashboardRooms>('/api/dashboard/rooms', { signal }),
    enabled,
  })
}

interface ChangeDialogProps {
  reservation: ReservationView
  onConflict: (error: Error) => boolean
  onClose: () => void
}

function SimpleChange({
  reservation,
  title,
  description,
  confirmLabel,
  danger = false,
  change,
  onDone,
  onConflict,
  onClose,
}: ChangeDialogProps & {
  title: string
  description: ReactNode
  confirmLabel: string
  danger?: boolean
  change: ReservationChange
  onDone: () => void
}) {
  const mutation = useReservationChange(reservation.id)
  return (
    <ConfirmDialog
      title={title}
      description={description}
      confirmLabel={confirmLabel}
      danger={danger}
      pending={mutation.isPending}
      error={mutation.isError ? changeErrorMessage(mutation.error) : null}
      onConfirm={() => {
        mutation.mutate(change, {
          onSuccess: onDone,
          onError: (error) => {
            onConflict(error)
          },
        })
      }}
      onCancel={onClose}
    />
  )
}

// --- cancel ------------------------------------------------------------------------------

function CancelDialog({
  reservation: r,
  onDone,
  onConflict,
  onClose,
}: ChangeDialogProps & { onDone: () => void }) {
  const mutation = useReservationChange(r.id)
  const [reason, setReason] = useState('')
  const [missing, setMissing] = useState(false)
  const reasonId = useId()
  const errorId = useId()
  const cancelRef = useRef<HTMLButtonElement>(null)
  const guest = `${r.guest.first_name} ${r.guest.last_name}`

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!reason.trim()) {
      setMissing(true)
      return
    }
    mutation.mutate(
      { kind: 'cancel', version: r.version, reason: reason.trim() },
      {
        onSuccess: onDone,
        onError: (error) => {
          onConflict(error)
        },
      },
    )
  }

  return (
    <Dialog
      title={`Cancel ${guest}'s booking?`}
      description={
        <>
          Booking <strong>{r.confirmation_code}</strong> for {guest}, {formatDate(r.check_in_date)}{' '}
          to {formatDate(r.check_out_date)}, will be cancelled and its nights released. This cannot
          be undone.
        </>
      }
      onClose={onClose}
      initialFocus={cancelRef}
    >
      <form onSubmit={submit} noValidate className="space-y-4">
        <FormAlert
          message={
            mutation.isError && !isConflict(mutation.error)
              ? changeErrorMessage(mutation.error)
              : null
          }
        />
        <div className="space-y-1">
          <label htmlFor={reasonId} className="block text-sm font-medium text-slate-700">
            Reason
          </label>
          <textarea
            id={reasonId}
            rows={3}
            maxLength={500}
            required
            value={reason}
            aria-invalid={missing ? true : undefined}
            aria-describedby={missing ? errorId : undefined}
            onChange={(e) => {
              setReason(e.target.value)
              setMissing(false)
            }}
            className={`${fieldClass} aria-invalid:border-red-600`}
          />
          {missing && (
            <p id={errorId} className="text-sm text-red-700">
              Enter why the booking is cancelled.
            </p>
          )}
        </div>
        <div className="flex justify-end gap-3">
          <button ref={cancelRef} type="button" onClick={onClose} className={buttonSecondary}>
            Keep booking
          </button>
          <button
            type="submit"
            disabled={mutation.isPending}
            className="inline-flex items-center justify-center gap-2 rounded-md bg-red-700 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-red-800 focus-visible:ring-2 focus-visible:ring-sky-600 focus-visible:ring-offset-2 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-60"
          >
            {mutation.isPending ? 'Working…' : 'Cancel booking'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

// --- rooms -------------------------------------------------------------------------------

function roomLabel(room: DashboardRoom): string {
  return `${room.number}: ${OCCUPANCY[room.occupancy_status].label}, ${HOUSEKEEPING[room.housekeeping_status].label}`
}

function RoomPicker({
  label,
  rooms,
  value,
  disabledReason,
  onChange,
}: {
  label: string
  rooms: DashboardRoom[]
  value: string
  disabledReason: (room: DashboardRoom) => string | null
  onChange: (value: string) => void
}) {
  const id = useId()
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-medium text-slate-700">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => {
          onChange(e.target.value)
        }}
        className={fieldClass}
      >
        <option value="">Choose a room</option>
        {rooms.map((room) => {
          const reason = disabledReason(room)
          return (
            <option key={room.room_id} value={room.room_id} disabled={reason !== null}>
              {roomLabel(room)}
              {reason ? ` (${reason})` : ''}
            </option>
          )
        })}
      </select>
    </div>
  )
}

function AssignRoomDialog({
  reservation: r,
  rooms,
  onDone,
  onConflict,
  onClose,
}: ChangeDialogProps & { rooms: DashboardRoom[] | null; onDone: (number: string) => void }) {
  const mutation = useReservationChange(r.id)
  const [selected, setSelected] = useState('')
  const ofType = (rooms ?? []).filter((room) => room.room_type === r.room_type)
  const room = ofType.find((x) => String(x.room_id) === selected)

  return (
    <Dialog
      title={`Assign a room to ${r.guest.first_name} ${r.guest.last_name}`}
      description={
        <>
          {r.room_type_name}, {formatDate(r.check_in_date)} to {formatDate(r.check_out_date)}. The
          room must be free for these nights; it is cleaned before arrival.
        </>
      }
      onClose={onClose}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (!room) return
          mutation.mutate(
            { kind: 'assign-room', version: r.version, roomId: room.room_id },
            {
              onSuccess: () => {
                onDone(room.number)
              },
              onError: (error) => {
                onConflict(error)
              },
            },
          )
        }}
        className="space-y-4"
      >
        <FormAlert
          message={
            mutation.isError && !isConflict(mutation.error)
              ? changeErrorMessage(mutation.error)
              : null
          }
        />
        {rooms === null ? (
          <p role="status" className="text-sm text-slate-600">
            Loading rooms…
          </p>
        ) : (
          <RoomPicker
            label={`${r.room_type_name} room`}
            rooms={ofType}
            value={selected}
            disabledReason={(x) => (x.room_id === r.room_id ? 'assigned now' : null)}
            onChange={setSelected}
          />
        )}
        <div className="flex justify-end gap-3">
          <button type="button" onClick={onClose} className={buttonSecondary}>
            Cancel
          </button>
          <button type="submit" disabled={!room || mutation.isPending} className={buttonPrimary}>
            {mutation.isPending ? 'Working…' : 'Assign room'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

function roomActionMessage(error: Error): string {
  if (isConflict(error)) {
    return 'Someone else changed this booking or the room while you were looking at it. The latest version is now shown; check it and try again.'
  }
  return changeErrorMessage(error)
}

function CheckInDialog({
  reservation: r,
  rooms,
  onDone,
  onConflict,
  onClose,
}: ChangeDialogProps & { rooms: DashboardRoom[] | null; onDone: (number: string) => void }) {
  const action = useRoomAction()
  const [selected, setSelected] = useState(r.room_id === null ? '' : String(r.room_id))
  const ofType = (rooms ?? []).filter((room) => room.room_type === r.room_type)
  const room = ofType.find((x) => String(x.room_id) === selected)

  function notReady(x: DashboardRoom): string | null {
    const held = x.reservation_id !== null && x.reservation_id !== r.id
    if (x.occupancy_status === 'occupied' || x.occupancy_status === 'departing_today')
      return 'occupied'
    if (held) return 'held for another guest'
    if (!READY.has(x.housekeeping_status)) return 'not ready'
    return null
  }

  return (
    <Dialog
      title={`Check in ${r.guest.first_name} ${r.guest.last_name}`}
      description={
        <>
          {r.confirmation_code}: {r.room_type_name}, {plural(r.nights, 'night')} until{' '}
          {formatDate(r.check_out_date)}.
        </>
      }
      onClose={onClose}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (!room) return
          action.mutate(
            {
              kind: 'check-in',
              reservationId: r.id,
              roomId: room.room_id,
              roomVersion: room.version,
            },
            {
              onSuccess: () => {
                onDone(room.number)
              },
              onError: (error) => {
                onConflict(error)
              },
            },
          )
        }}
        className="space-y-4"
      >
        <FormAlert
          message={
            action.isError && !isConflict(action.error) ? roomActionMessage(action.error) : null
          }
        />
        {rooms === null ? (
          <p role="status" className="text-sm text-slate-600">
            Loading rooms…
          </p>
        ) : (
          <RoomPicker
            label="Room"
            rooms={ofType}
            value={selected}
            disabledReason={notReady}
            onChange={setSelected}
          />
        )}
        <div className="flex justify-end gap-3">
          <button type="button" onClick={onClose} className={buttonSecondary}>
            Cancel
          </button>
          <button type="submit" disabled={!room || action.isPending} className={buttonPrimary}>
            {action.isPending ? 'Working…' : 'Check in'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

function CheckOutDialog({
  reservation: r,
  today,
  room,
  onDone,
  onConflict,
  onClose,
}: ChangeDialogProps & { today: string | null; room: DashboardRoom | null; onDone: () => void }) {
  const action = useRoomAction()
  const guest = `${r.guest.first_name} ${r.guest.last_name}`
  const early = today !== null && r.check_out_date > today
  return (
    <ConfirmDialog
      title={`Check out ${guest}?`}
      description={
        <>
          {guest} ({r.confirmation_code}) leaves room {r.room_number}, which becomes dirty.
          {early && (
            <>
              {' '}
              The booking runs until {formatDate(r.check_out_date)}: leaving early shortens the stay
              to today and removes the unused nights from the total.
            </>
          )}
        </>
      }
      confirmLabel="Check out"
      pending={action.isPending}
      error={
        room === null
          ? 'The room could not be loaded. Close this and try again.'
          : action.isError && !isConflict(action.error)
            ? roomActionMessage(action.error)
            : null
      }
      onConfirm={() => {
        if (!room) return
        action.mutate(
          { kind: 'check-out', reservationId: r.id, roomVersion: room.version },
          {
            onSuccess: onDone,
            onError: (error) => {
              onConflict(error)
            },
          },
        )
      }}
      onCancel={onClose}
    />
  )
}

// --- modify ------------------------------------------------------------------------------

function ModifyDialog({
  reservation: r,
  onDone,
  onConflict,
  onClose,
}: ChangeDialogProps & { onDone: () => void }) {
  const { can } = usePermissions()
  const mutation = useReservationChange(r.id)
  const inHouse = r.status === 'checked_in'
  const [values, setValues] = useState({
    check_in_date: r.check_in_date,
    check_out_date: r.check_out_date,
    adults: String(r.adults),
    children: String(r.children),
    room_type_id: String(r.room_type_id),
    rate: '',
    notes: r.notes ?? '',
    is_vip: r.is_vip,
  })
  const [error, setError] = useState<string | null>(null)
  const notesId = useId()
  const typeId = useId()
  const vipId = useId()

  const datesValid = values.check_out_date > values.check_in_date
  const availability = useQuery({
    queryKey: [
      'reservations',
      'availability',
      values.check_in_date,
      values.check_out_date,
      values.adults,
      values.children,
    ],
    queryFn: ({ signal }) =>
      apiFetch<Availability>(
        `/api/reservations/availability?${new URLSearchParams({
          check_in: values.check_in_date,
          check_out: values.check_out_date,
          adults: values.adults,
          children: values.children,
        }).toString()}`,
        { signal },
      ),
    // Changing the room type is for confirmed bookings, and the list needs reservation.create.
    enabled: !inHouse && datesValid && can(P.RESERVATION_CREATE),
    meta: { handlesForbidden: true },
    retry: false,
  })

  function set<K extends keyof typeof values>(field: K, value: (typeof values)[K]) {
    setValues((current) => ({ ...current, [field]: value }))
    setError(null)
  }

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!datesValid) {
      setError('Departure must be after arrival.')
      return
    }
    const body: ReservationUpdate = { version: r.version }
    if (values.check_out_date !== r.check_out_date) body.check_out_date = values.check_out_date
    if ((values.notes.trim() || null) !== r.notes) body.notes = values.notes.trim() || null
    if (!inHouse) {
      if (values.check_in_date !== r.check_in_date) body.check_in_date = values.check_in_date
      if (Number(values.adults) !== r.adults) body.adults = Number(values.adults)
      if (Number(values.children) !== r.children) body.children = Number(values.children)
      if (Number(values.room_type_id) !== r.room_type_id) {
        body.room_type_id = Number(values.room_type_id)
      }
      if (values.is_vip !== r.is_vip) body.is_vip = values.is_vip
    }
    if (values.rate.trim()) {
      const minor = parseAmount(values.rate)
      if (minor === null) {
        setError('Enter the price per night as a number, like 1299 or 1299.50.')
        return
      }
      body.nightly_rate_minor = minor
    }
    if (Object.keys(body).length === 1) {
      onClose()
      return
    }
    mutation.mutate(
      { kind: 'modify', body },
      {
        onSuccess: onDone,
        onError: (e) => {
          if (!onConflict(e)) setError(changeErrorMessage(e))
        },
      },
    )
  }

  const types = availability.data?.room_types ?? []
  const typeOptions = types.some((t) => t.room_type_id === r.room_type_id)
    ? types
    : [
        {
          room_type_id: r.room_type_id,
          name: r.room_type_name,
          available: 0,
          total_minor: 0,
          currency: r.currency,
        },
        ...types,
      ]

  return (
    <Dialog
      title={`Change booking ${r.confirmation_code}`}
      description={
        inHouse ? (
          <>
            {r.guest.first_name} {r.guest.last_name} is checked in: only the departure date and
            notes can change.
          </>
        ) : (
          <>
            For {r.guest.first_name} {r.guest.last_name}. Nights that stay keep their price; new
            nights are charged at today's rate.
          </>
        )
      }
      onClose={onClose}
    >
      <form onSubmit={submit} noValidate className="space-y-4">
        <FormAlert message={error} />
        <div className="grid gap-3 sm:grid-cols-2">
          <Field
            label="Arrival"
            type="date"
            required
            disabled={inHouse}
            value={values.check_in_date}
            onChange={(e) => {
              set('check_in_date', e.target.value)
            }}
          />
          <Field
            label="Departure"
            type="date"
            required
            value={values.check_out_date}
            onChange={(e) => {
              set('check_out_date', e.target.value)
            }}
          />
          <Field
            label="Adults"
            type="number"
            min={1}
            max={12}
            required
            disabled={inHouse}
            value={values.adults}
            onChange={(e) => {
              set('adults', e.target.value)
            }}
          />
          <Field
            label="Children"
            type="number"
            min={0}
            max={12}
            required
            disabled={inHouse}
            value={values.children}
            onChange={(e) => {
              set('children', e.target.value)
            }}
          />
        </div>
        {!inHouse && can(P.RESERVATION_CREATE) && (
          <div className="space-y-1">
            <label htmlFor={typeId} className="block text-sm font-medium text-slate-700">
              Room type
            </label>
            <select
              id={typeId}
              value={values.room_type_id}
              onChange={(e) => {
                set('room_type_id', e.target.value)
              }}
              className={fieldClass}
            >
              {typeOptions.map((t) => (
                <option
                  key={t.room_type_id}
                  value={t.room_type_id}
                  disabled={t.room_type_id !== r.room_type_id && t.available < 1}
                >
                  {t.name}
                  {t.room_type_id === r.room_type_id
                    ? ' (booked now)'
                    : t.available < 1
                      ? ' (full)'
                      : `, ${plural(t.available, 'room')} free, ${formatMoney(t.total_minor, t.currency)} at today's rates`}
                </option>
              ))}
            </select>
          </div>
        )}
        {can(P.RESERVATION_OVERRIDE_RATE) && (
          <Field
            label={`New price per night (${r.currency})`}
            inputMode="decimal"
            hint="Applies to every night of the stay. Leave empty to keep the prices."
            value={values.rate}
            onChange={(e) => {
              set('rate', e.target.value)
            }}
          />
        )}
        <div className="space-y-1">
          <label htmlFor={notesId} className="block text-sm font-medium text-slate-700">
            Notes <span className="font-normal text-slate-500">(optional)</span>
          </label>
          <textarea
            id={notesId}
            rows={3}
            maxLength={2000}
            value={values.notes}
            onChange={(e) => {
              set('notes', e.target.value)
            }}
            className={fieldClass}
          />
        </div>
        {!inHouse && (
          <div className="flex items-center gap-2">
            <input
              id={vipId}
              type="checkbox"
              checked={values.is_vip}
              onChange={(e) => {
                set('is_vip', e.target.checked)
              }}
              className="size-4 rounded border-slate-300"
            />
            <label htmlFor={vipId} className="text-sm text-slate-800">
              VIP guest
            </label>
          </div>
        )}
        <div className="flex justify-end gap-3">
          <button type="button" onClick={onClose} className={buttonSecondary}>
            Cancel
          </button>
          <button type="submit" disabled={mutation.isPending} className={buttonPrimary}>
            {mutation.isPending ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}
