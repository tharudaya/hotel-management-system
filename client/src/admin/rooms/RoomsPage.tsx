import { Plus } from 'lucide-react'
import { Fragment, type SubmitEvent, useId, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'

import { ApiError } from '../../api/client'
import type { RoomOut, RoomTypeOut, RoomUpdate } from '../../api/types'
import { FormAlert } from '../../components/AuthShell'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Dialog } from '../../components/Dialog'
import { GuardedButton } from '../../components/GuardedButton'
import { buttonPrimary, buttonSecondary, fieldClass } from '../../components/styles'
import { plural } from '../../lib/format'
import { useDocumentTitle } from '../../lib/useDocumentTitle'
import { Field } from '../users/Field'
import { messageFor } from '../users/form'
import { ActiveBadge } from './ActiveBadge'
import {
  hasFilters,
  NO_FILTERS,
  parseFilters,
  type RoomFilters,
  STATUS_FILTER_LABELS,
  STATUS_FILTERS,
  toSearch,
} from './params'
import {
  isConflict,
  useCreateRoom,
  useRooms,
  useRoomStatus,
  useRoomTypes,
  useUpdateRoom,
} from './queries'
import { TextAreaField } from './TextAreaField'

const NUMBER_PATTERN = /^[A-Za-z0-9-]{1,10}$/
const MAX_FLOOR = 200

/** Told to the admin when their copy of a room was out of date. */
function conflictMessage(number: string): string {
  return `Room ${number} was changed by someone else while you were working on it. The list now shows the latest version; check it and try again.`
}

/** /admin/rooms */
export function RoomsPage() {
  useDocumentTitle('Rooms')
  const [search, setSearch] = useSearchParams()
  const filters = parseFilters(search)
  const rooms = useRooms(filters)
  // Every floor and type, for the filter options (independent of the current filter).
  const allRooms = useRooms({ ...NO_FILTERS, status: 'all' })
  const types = useRoomTypes(true)
  const [notice, setNotice] = useState<string | null>(null)
  const [alert, setAlert] = useState<string | null>(null)
  const [editing, setEditing] = useState<RoomOut | 'new' | null>(null)
  const [statusChange, setStatusChange] = useState<RoomOut | null>(null)

  const activeTypes = types.data?.filter((t) => t.is_active) ?? []
  const floors = [...new Set(allRooms.data?.map((r) => r.floor) ?? [])].sort((a, b) => a - b)

  function update(changes: Partial<RoomFilters>) {
    setSearch(toSearch({ ...filters, ...changes }), { replace: true })
  }

  function report(result: { notice?: string; conflict?: string }) {
    setNotice(result.notice ?? null)
    setAlert(result.conflict ?? null)
  }

  return (
    <section aria-labelledby="rooms-heading" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 id="rooms-heading" className="text-xl font-semibold text-slate-900">
          Rooms
        </h1>
        <GuardedButton
          blockedReason={
            types.isSuccess && activeTypes.length === 0
              ? 'Add a room type first (Room types tab).'
              : null
          }
          onClick={() => {
            report({})
            setEditing('new')
          }}
          className={buttonPrimary}
        >
          <Plus aria-hidden="true" className="size-4" />
          Add room
        </GuardedButton>
      </div>

      <Filters filters={filters} floors={floors} types={types.data ?? []} onChange={update} />

      <p role="status" className={notice ? 'text-sm text-emerald-800' : 'sr-only'}>
        {notice}
      </p>
      {alert && (
        <p
          role="alert"
          className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950"
        >
          {alert}
        </p>
      )}

      <div className="rounded-lg bg-white shadow-sm">
        {rooms.isPending ? (
          <p role="status" className="p-6 text-slate-600">
            Loading rooms…
          </p>
        ) : rooms.isError ? (
          <div role="alert" className="space-y-3 p-6">
            <p className="text-slate-800">The rooms could not be loaded.</p>
            <button
              type="button"
              onClick={() => {
                void rooms.refetch()
              }}
              className={buttonSecondary}
            >
              Try again
            </button>
          </div>
        ) : rooms.data.length === 0 ? (
          <div className="space-y-3 p-6 text-slate-700">
            <p>
              {hasFilters(filters) ? 'No rooms match these filters.' : 'There are no rooms yet.'}
            </p>
            {hasFilters(filters) && (
              <button
                type="button"
                onClick={() => {
                  setSearch(toSearch(NO_FILTERS), { replace: true })
                }}
                className={buttonSecondary}
              >
                Clear filters
              </button>
            )}
          </div>
        ) : (
          <RoomTable
            rooms={rooms.data}
            onEdit={(room) => {
              report({})
              setEditing(room)
            }}
            onStatusChange={(room) => {
              report({})
              setStatusChange(room)
            }}
          />
        )}
      </div>

      {editing && (
        <RoomFormDialog
          room={editing === 'new' ? null : editing}
          activeTypes={activeTypes}
          allTypes={types.data ?? []}
          onClose={() => {
            setEditing(null)
          }}
          onDone={(result) => {
            setEditing(null)
            report(result)
          }}
        />
      )}
      {statusChange && (
        <RoomStatusDialog
          room={statusChange}
          onClose={() => {
            setStatusChange(null)
          }}
          onDone={(result) => {
            setStatusChange(null)
            report(result)
          }}
        />
      )}
    </section>
  )
}

function Filters({
  filters,
  floors,
  types,
  onChange,
}: {
  filters: RoomFilters
  floors: number[]
  types: RoomTypeOut[]
  onChange: (changes: Partial<RoomFilters>) => void
}) {
  const floorId = useId()
  const typeId = useId()
  const statusId = useId()
  return (
    <form
      role="search"
      aria-label="Filter rooms"
      onSubmit={(e) => {
        e.preventDefault()
      }}
      className="grid gap-3 rounded-lg bg-white p-4 shadow-sm sm:grid-cols-3"
    >
      <div className="space-y-1">
        <label htmlFor={floorId} className="block text-sm font-medium text-slate-700">
          Floor
        </label>
        <select
          id={floorId}
          value={filters.floor ?? ''}
          onChange={(e) => {
            onChange({ floor: e.target.value === '' ? null : Number(e.target.value) })
          }}
          className={fieldClass}
        >
          <option value="">All floors</option>
          {floors.map((floor) => (
            <option key={floor} value={floor}>
              Floor {floor}
            </option>
          ))}
        </select>
      </div>
      <div className="space-y-1">
        <label htmlFor={typeId} className="block text-sm font-medium text-slate-700">
          Room type
        </label>
        <select
          id={typeId}
          value={filters.typeId ?? ''}
          onChange={(e) => {
            onChange({ typeId: e.target.value === '' ? null : Number(e.target.value) })
          }}
          className={fieldClass}
        >
          <option value="">All room types</option>
          {types.map((type) => (
            <option key={type.id} value={type.id}>
              {type.code} – {type.name}
              {type.is_active ? '' : ' (retired)'}
            </option>
          ))}
        </select>
      </div>
      <div className="space-y-1">
        <label htmlFor={statusId} className="block text-sm font-medium text-slate-700">
          Status
        </label>
        <select
          id={statusId}
          value={filters.status}
          onChange={(e) => {
            const value = e.target.value
            onChange({ status: STATUS_FILTERS.find((s) => s === value) ?? 'active' })
          }}
          className={fieldClass}
        >
          {STATUS_FILTERS.map((status) => (
            <option key={status} value={status}>
              {STATUS_FILTER_LABELS[status]}
            </option>
          ))}
        </select>
      </div>
    </form>
  )
}

/** Rooms grouped by floor: one row group per floor, headed "Floor 3 (10 rooms)". */
function RoomTable({
  rooms,
  onEdit,
  onStatusChange,
}: {
  rooms: RoomOut[]
  onEdit: (room: RoomOut) => void
  onStatusChange: (room: RoomOut) => void
}) {
  const byFloor = new Map<number, RoomOut[]>()
  for (const room of rooms.slice().sort((a, b) => a.floor - b.floor)) {
    byFloor.set(room.floor, [...(byFloor.get(room.floor) ?? []), room])
  }
  return (
    <div className="overflow-x-auto">
      <p className="border-b border-slate-200 px-4 py-3 text-sm text-slate-600">
        {plural(rooms.length, 'room')}
      </p>
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Rooms by floor</caption>
        <thead className="bg-slate-50 text-slate-700">
          <tr>
            {['Room', 'Room type', 'Notes', 'Status'].map((label) => (
              <th key={label} scope="col" className="px-4 py-2 font-semibold">
                {label}
              </th>
            ))}
            <th scope="col" className="px-4 py-2">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        {[...byFloor].map(([floor, floorRooms]) => (
          <tbody key={floor} className="divide-y divide-slate-100">
            <tr className="bg-slate-50/60">
              <th
                scope="rowgroup"
                colSpan={5}
                className="px-4 pt-3 pb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase"
              >
                Floor {floor} ({plural(floorRooms.length, 'room')})
              </th>
            </tr>
            {floorRooms.map((room) => (
              <Fragment key={room.id}>
                <tr>
                  <th scope="row" className="px-4 py-2 font-medium text-slate-900">
                    {room.number}
                  </th>
                  <td className="px-4 py-2 text-slate-700">
                    <span className="font-mono">{room.room_type.code}</span> {room.room_type.name}
                    {!room.room_type.is_active && (
                      <span className="ml-1 text-xs text-slate-500">(type retired)</span>
                    )}
                  </td>
                  <td
                    className="max-w-xs truncate px-4 py-2 text-slate-700"
                    title={room.notes ?? ''}
                  >
                    {room.notes ?? <span className="text-slate-400">None</span>}
                  </td>
                  <td className="px-4 py-2">
                    <span className="flex flex-wrap items-center gap-2">
                      <ActiveBadge active={room.is_active} />
                      {room.guest_in_house && (
                        <span className="text-xs text-slate-600">Guest in house</span>
                      )}
                      {room.upcoming_reservations > 0 && (
                        <span className="text-xs text-slate-600">
                          {plural(room.upcoming_reservations, 'upcoming booking')}
                        </span>
                      )}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <div className="flex justify-end gap-2">
                      <button
                        type="button"
                        aria-label={`Edit room ${room.number}`}
                        onClick={() => {
                          onEdit(room)
                        }}
                        className={buttonSecondary}
                      >
                        Edit
                      </button>
                      <GuardedButton
                        aria-label={`${room.is_active ? 'Retire' : 'Reactivate'} room ${room.number}`}
                        blockedReason={room.is_active ? room.retire_blocked : null}
                        onClick={() => {
                          onStatusChange(room)
                        }}
                        className={buttonSecondary}
                      >
                        {room.is_active ? 'Retire' : 'Reactivate'}
                      </GuardedButton>
                    </div>
                  </td>
                </tr>
              </Fragment>
            ))}
          </tbody>
        ))}
      </table>
    </div>
  )
}

type Done = (result: { notice?: string; conflict?: string }) => void

function RoomStatusDialog({
  room,
  onClose,
  onDone,
}: {
  room: RoomOut
  onClose: () => void
  onDone: Done
}) {
  const retiring = room.is_active
  const change = useRoomStatus(room.id, retiring ? 'retire' : 'reactivate')
  return (
    <ConfirmDialog
      title={retiring ? `Retire room ${room.number}?` : `Reactivate room ${room.number}?`}
      description={
        retiring
          ? `Room ${room.number} leaves the front desk dashboard and can no longer be booked or checked in to. Its history stays, and you can reactivate it later.`
          : `Room ${room.number} returns to the front desk dashboard.`
      }
      confirmLabel={retiring ? 'Retire room' : 'Reactivate room'}
      danger={retiring}
      pending={change.isPending}
      // The server's own words (e.g. "Room 304 has a guest checked in.").
      error={change.isError && !isConflict(change.error) ? messageFor(change.error) : null}
      onConfirm={() => {
        change.mutate(room.version, {
          onSuccess: () => {
            onDone({ notice: `Room ${room.number} was ${retiring ? 'retired' : 'reactivated'}.` })
          },
          onError: (error) => {
            if (isConflict(error)) onDone({ conflict: conflictMessage(room.number) })
          },
        })
      }}
      onCancel={onClose}
    />
  )
}

type RoomField = 'number' | 'floor' | 'room_type_id'

function RoomFormDialog({
  room,
  activeTypes,
  allTypes,
  onClose,
  onDone,
}: {
  room: RoomOut | null
  activeTypes: RoomTypeOut[]
  allTypes: RoomTypeOut[]
  onClose: () => void
  onDone: Done
}) {
  const create = useCreateRoom()
  const update = useUpdateRoom(room?.id ?? 0)
  const saving = room ? update : create
  const [values, setValues] = useState({
    number: room?.number ?? '',
    floor: room ? String(room.floor) : '',
    room_type_id: room ? String(room.room_type.id) : '',
    notes: room?.notes ?? '',
  })
  const [errors, setErrors] = useState<Partial<Record<RoomField, string>>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const numberRef = useRef<HTMLInputElement>(null)
  const floorRef = useRef<HTMLInputElement>(null)
  const typeRef = useRef<HTMLSelectElement>(null)
  const typeId = useId()
  const typeHintId = useId()
  const typeErrorId = useId()

  // Only active types can be chosen; a room's current (retired) type is shown, disabled.
  const currentType = room ? allTypes.find((t) => t.id === room.room_type.id) : undefined
  const options = [...activeTypes, ...(currentType && !currentType.is_active ? [currentType] : [])]
  const typeLocked = room?.type_change_blocked ?? null

  function set(field: keyof typeof values, value: string) {
    setValues((current) => ({ ...current, [field]: value }))
    setErrors((current) => ({ ...current, [field]: undefined }))
  }

  function showError(field: RoomField, message: string) {
    setErrors({ [field]: message })
    ;({ number: numberRef, floor: floorRef, room_type_id: typeRef })[field].current?.focus()
  }

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    setFormError(null)
    const floor = Number(values.floor)
    if (!NUMBER_PATTERN.test(values.number.trim())) {
      showError('number', 'Use 1–10 letters, digits or hyphens, e.g. 304 or PH1.')
      return
    }
    if (values.floor.trim() === '' || !Number.isInteger(floor) || floor < 0 || floor > MAX_FLOOR) {
      showError('floor', `Enter a floor from 0 to ${MAX_FLOOR}.`)
      return
    }
    if (!values.room_type_id) {
      showError('room_type_id', 'Choose a room type.')
      return
    }
    const next = {
      number: values.number.trim(),
      floor,
      room_type_id: Number(values.room_type_id),
      notes: values.notes.trim() || null,
    }
    const onError = (error: Error) => {
      if (room && isConflict(error)) {
        onDone({ conflict: conflictMessage(room.number) })
      } else if (error instanceof ApiError && error.code === 'room_number_taken') {
        showError('number', error.message)
      } else if (error instanceof ApiError && error.code === 'room_type_retired') {
        showError('room_type_id', error.message)
      } else {
        setFormError(messageFor(error))
      }
    }
    if (!room) {
      create.mutate(next, {
        onSuccess: (created) => {
          onDone({ notice: `Room ${created.number} was added.` })
        },
        onError,
      })
      return
    }
    const body: RoomUpdate = { version: room.version }
    if (next.number.toUpperCase() !== room.number) body.number = next.number
    if (next.floor !== room.floor) body.floor = next.floor
    if (next.room_type_id !== room.room_type.id) body.room_type_id = next.room_type_id
    if (next.notes !== room.notes) body.notes = next.notes
    if (Object.keys(body).length === 1) {
      onClose()
      return
    }
    update.mutate(body, {
      onSuccess: (saved) => {
        onDone({ notice: `Room ${saved.number} was saved.` })
      },
      onError,
    })
  }

  const describedBy =
    [errors.room_type_id ? typeErrorId : null, typeLocked ? typeHintId : null]
      .filter(Boolean)
      .join(' ') || undefined

  return (
    <Dialog title={room ? `Edit room ${room.number}` : 'Add room'} onClose={onClose}>
      <form onSubmit={handleSubmit} noValidate className="space-y-4">
        <FormAlert message={formError} />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            ref={numberRef}
            label="Room number"
            required
            maxLength={10}
            autoCapitalize="characters"
            spellCheck={false}
            value={values.number}
            error={errors.number}
            onChange={(e) => {
              set('number', e.target.value)
            }}
          />
          <Field
            ref={floorRef}
            label="Floor"
            required
            type="number"
            min={0}
            max={MAX_FLOOR}
            value={values.floor}
            error={errors.floor}
            onChange={(e) => {
              set('floor', e.target.value)
            }}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor={typeId} className="block text-sm font-medium text-slate-700">
            Room type
          </label>
          <select
            ref={typeRef}
            id={typeId}
            required
            disabled={typeLocked !== null}
            value={values.room_type_id}
            aria-invalid={errors.room_type_id ? true : undefined}
            aria-describedby={describedBy}
            onChange={(e) => {
              set('room_type_id', e.target.value)
            }}
            className={`${fieldClass} disabled:bg-slate-100 aria-invalid:border-red-600`}
          >
            {!room && <option value="">Choose a room type</option>}
            {options.map((type) => (
              <option key={type.id} value={type.id} disabled={!type.is_active}>
                {type.code} – {type.name}
                {type.is_active ? '' : ' (retired)'}
              </option>
            ))}
          </select>
          {errors.room_type_id && (
            <p id={typeErrorId} className="text-sm text-red-700">
              {errors.room_type_id}
            </p>
          )}
          {typeLocked && (
            <p id={typeHintId} className="text-xs text-slate-600">
              {typeLocked} The room type can be changed after check-out.
            </p>
          )}
        </div>
        <TextAreaField
          label="Notes"
          maxLength={2000}
          hint="For staff, e.g. connecting door to 305."
          value={values.notes}
          onChange={(e) => {
            set('notes', e.target.value)
          }}
        />
        <div className="flex justify-end gap-3">
          <button type="button" onClick={onClose} className={buttonSecondary}>
            Cancel
          </button>
          <button type="submit" disabled={saving.isPending} className={buttonPrimary}>
            {saving.isPending ? 'Saving…' : room ? 'Save room' : 'Add room'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}
