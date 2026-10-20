import { Plus } from 'lucide-react'
import { type SubmitEvent, useId, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'

import { ApiError } from '../../api/client'
import type { RoomTypeOut, RoomTypeUpdate } from '../../api/types'
import { FormAlert } from '../../components/AuthShell'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Dialog } from '../../components/Dialog'
import { GuardedButton } from '../../components/GuardedButton'
import { buttonPrimary, buttonSecondary, focusRing } from '../../components/styles'
import { useHotelInfo } from '../../dashboard/queries'
import { amountInput, formatMoney, parseAmount } from '../../lib/format'
import { useDocumentTitle } from '../../lib/useDocumentTitle'
import { Field } from '../users/Field'
import { messageFor } from '../users/form'
import { ActiveBadge } from './ActiveBadge'
import { useCreateRoomType, useRoomTypes, useRoomTypeStatus, useUpdateRoomType } from './queries'
import { TextAreaField } from './TextAreaField'

const MAX_GUESTS = 12
const CODE_PATTERN = /^[A-Za-z0-9_-]{1,20}$/

/** /admin/rooms/types */
export function RoomTypesPage() {
  const currency = useHotelInfo().data?.currency ?? 'SEK'
  useDocumentTitle('Room types')
  const [search, setSearch] = useSearchParams()
  const showRetired = search.get('show') === 'retired'
  const types = useRoomTypes(showRetired)
  const [notice, setNotice] = useState<string | null>(null)
  const [editing, setEditing] = useState<RoomTypeOut | 'new' | null>(null)
  const [statusChange, setStatusChange] = useState<RoomTypeOut | null>(null)
  const showRetiredId = useId()

  return (
    <section aria-labelledby="room-types-heading" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 id="room-types-heading" className="text-xl font-semibold text-slate-900">
          Room types
        </h1>
        <button
          type="button"
          onClick={() => {
            setNotice(null)
            setEditing('new')
          }}
          className={buttonPrimary}
        >
          <Plus aria-hidden="true" className="size-4" />
          Add room type
        </button>
      </div>
      <div className="flex items-center gap-2">
        <input
          id={showRetiredId}
          type="checkbox"
          checked={showRetired}
          onChange={(e) => {
            setSearch(e.target.checked ? { show: 'retired' } : {}, { replace: true })
          }}
          className={`size-4 rounded border-slate-300 ${focusRing}`}
        />
        <label htmlFor={showRetiredId} className="text-sm text-slate-700">
          Show retired room types
        </label>
      </div>
      <p role="status" className={notice ? 'text-sm text-emerald-800' : 'sr-only'}>
        {notice}
      </p>

      <div className="rounded-lg bg-white shadow-sm">
        {types.isPending ? (
          <p role="status" className="p-6 text-slate-600">
            Loading room types…
          </p>
        ) : types.isError ? (
          <div role="alert" className="space-y-3 p-6">
            <p className="text-slate-800">The room types could not be loaded.</p>
            <button
              type="button"
              onClick={() => {
                void types.refetch()
              }}
              className={buttonSecondary}
            >
              Try again
            </button>
          </div>
        ) : types.data.length === 0 ? (
          <p className="p-6 text-slate-700">
            {showRetired
              ? 'There are no room types yet.'
              : 'There are no active room types. Add one to start setting up rooms.'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Room types</caption>
              <thead className="bg-slate-50 text-slate-700">
                <tr>
                  {[
                    'Code',
                    'Name',
                    'Max guests',
                    'Beds',
                    'Price per night',
                    'Active rooms',
                    'Status',
                  ].map((label) => (
                    <th key={label} scope="col" className="px-4 py-2 font-semibold">
                      {label}
                    </th>
                  ))}
                  <th scope="col" className="px-4 py-2">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {types.data.map((type) => (
                  <tr key={type.id}>
                    <th scope="row" className="px-4 py-2 font-mono font-medium text-slate-900">
                      {type.code}
                    </th>
                    <td className="px-4 py-2 text-slate-900">{type.name}</td>
                    <td className="px-4 py-2 text-slate-700 tabular-nums">{type.max_occupancy}</td>
                    <td className="px-4 py-2 text-slate-700">
                      {type.bed_setup ?? <span className="text-slate-400">Not set</span>}
                    </td>
                    <td className="px-4 py-2 whitespace-nowrap text-slate-700 tabular-nums">
                      {type.base_rate_minor > 0 ? (
                        formatMoney(type.base_rate_minor, currency)
                      ) : (
                        <span className="text-amber-800">Not set</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-slate-700 tabular-nums">
                      {type.active_room_count}
                    </td>
                    <td className="px-4 py-2">
                      <ActiveBadge active={type.is_active} />
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex justify-end gap-2">
                        <button
                          type="button"
                          aria-label={`Edit room type ${type.code}`}
                          onClick={() => {
                            setNotice(null)
                            setEditing(type)
                          }}
                          className={buttonSecondary}
                        >
                          Edit
                        </button>
                        <GuardedButton
                          aria-label={`${type.is_active ? 'Retire' : 'Reactivate'} room type ${type.code}`}
                          blockedReason={type.is_active ? type.retire_blocked : null}
                          onClick={() => {
                            setNotice(null)
                            setStatusChange(type)
                          }}
                          className={buttonSecondary}
                        >
                          {type.is_active ? 'Retire' : 'Reactivate'}
                        </GuardedButton>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {editing && (
        <RoomTypeFormDialog
          roomType={editing === 'new' ? null : editing}
          onClose={() => {
            setEditing(null)
          }}
          onSaved={(message) => {
            setEditing(null)
            setNotice(message)
          }}
        />
      )}
      {statusChange && (
        <StatusDialog
          roomType={statusChange}
          onClose={() => {
            setStatusChange(null)
          }}
          onDone={(message) => {
            setStatusChange(null)
            setNotice(message)
          }}
        />
      )}
    </section>
  )
}

function StatusDialog({
  roomType,
  onClose,
  onDone,
}: {
  roomType: RoomTypeOut
  onClose: () => void
  onDone: (message: string) => void
}) {
  const retiring = roomType.is_active
  const change = useRoomTypeStatus(roomType.id, retiring ? 'retire' : 'reactivate')
  const name = `${roomType.code} (${roomType.name})`
  return (
    <ConfirmDialog
      title={retiring ? `Retire room type ${name}?` : `Reactivate room type ${name}?`}
      description={
        retiring
          ? 'It can no longer be given to rooms. Its history stays, and you can reactivate it later.'
          : 'Rooms can be given this type again.'
      }
      confirmLabel={retiring ? 'Retire room type' : 'Reactivate room type'}
      danger={retiring}
      pending={change.isPending}
      error={change.isError ? messageFor(change.error) : null}
      onConfirm={() => {
        change.mutate(undefined, {
          onSuccess: () => {
            onDone(`Room type ${roomType.code} was ${retiring ? 'retired' : 'reactivated'}.`)
          },
        })
      }}
      onCancel={onClose}
    />
  )
}

type TypeField = 'code' | 'name' | 'max_occupancy' | 'rate'

function RoomTypeFormDialog({
  roomType,
  onClose,
  onSaved,
}: {
  roomType: RoomTypeOut | null
  onClose: () => void
  onSaved: (message: string) => void
}) {
  const create = useCreateRoomType()
  const update = useUpdateRoomType(roomType?.id ?? 0)
  const saving = roomType ? update : create
  const [values, setValues] = useState({
    code: roomType?.code ?? '',
    name: roomType?.name ?? '',
    max_occupancy: String(roomType?.max_occupancy ?? 2),
    bed_setup: roomType?.bed_setup ?? '',
    description: roomType?.description ?? '',
    rate: roomType && roomType.base_rate_minor > 0 ? amountInput(roomType.base_rate_minor) : '',
  })
  const currency = useHotelInfo().data?.currency ?? 'SEK'
  const [errors, setErrors] = useState<Partial<Record<TypeField, string>>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const codeRef = useRef<HTMLInputElement>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  const guestsRef = useRef<HTMLInputElement>(null)
  const rateRef = useRef<HTMLInputElement>(null)

  function set(field: keyof typeof values, value: string) {
    setValues((current) => ({ ...current, [field]: value }))
    setErrors((current) => ({ ...current, [field]: undefined }))
  }

  function showError(field: TypeField, message: string) {
    setErrors({ [field]: message })
    ;({ code: codeRef, name: nameRef, max_occupancy: guestsRef, rate: rateRef })[
      field
    ].current?.focus()
  }

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    setFormError(null)
    const guests = Number(values.max_occupancy)
    if (!CODE_PATTERN.test(values.code.trim())) {
      showError('code', 'Use 1–20 letters, digits, hyphens or underscores, e.g. DLX.')
      return
    }
    if (!values.name.trim()) {
      showError('name', 'Enter a name, e.g. Deluxe King.')
      return
    }
    if (!Number.isInteger(guests) || guests < 1 || guests > MAX_GUESTS) {
      showError('max_occupancy', `Enter a number from 1 to ${MAX_GUESTS}.`)
      return
    }
    const rate = parseAmount(values.rate)
    if (rate === null || rate < 1) {
      showError('rate', 'Enter the price per night, like 1299 or 1299.50.')
      return
    }
    const next = {
      code: values.code.trim(),
      name: values.name.trim(),
      max_occupancy: guests,
      bed_setup: values.bed_setup.trim() || null,
      description: values.description.trim() || null,
      base_rate_minor: rate,
    }
    const onError = (error: Error) => {
      if (error instanceof ApiError && error.code === 'room_type_code_taken') {
        showError('code', error.message)
      } else if (error instanceof ApiError && error.code === 'room_type_name_taken') {
        showError('name', error.message)
      } else {
        setFormError(messageFor(error))
      }
    }
    if (!roomType) {
      create.mutate(next, {
        onSuccess: (created) => {
          onSaved(`Room type ${created.code} was added.`)
        },
        onError,
      })
      return
    }
    // Only what changed.
    const body: RoomTypeUpdate = {}
    if (next.code.toUpperCase() !== roomType.code) body.code = next.code
    if (next.name !== roomType.name) body.name = next.name
    if (next.max_occupancy !== roomType.max_occupancy) body.max_occupancy = next.max_occupancy
    if (next.bed_setup !== roomType.bed_setup) body.bed_setup = next.bed_setup
    if (next.description !== roomType.description) body.description = next.description
    if (next.base_rate_minor !== roomType.base_rate_minor)
      body.base_rate_minor = next.base_rate_minor
    if (Object.keys(body).length === 0) {
      onClose()
      return
    }
    update.mutate(body, {
      onSuccess: (saved) => {
        onSaved(`Room type ${saved.code} was saved.`)
      },
      onError,
    })
  }

  return (
    <Dialog
      title={roomType ? `Edit room type ${roomType.code}` : 'Add room type'}
      onClose={onClose}
    >
      <form onSubmit={handleSubmit} noValidate className="space-y-4">
        <FormAlert message={formError} />
        <div className="grid gap-4 sm:grid-cols-[1fr_2fr]">
          <Field
            ref={codeRef}
            label="Code"
            required
            maxLength={20}
            autoCapitalize="characters"
            spellCheck={false}
            hint="Short and unique, e.g. DLX."
            value={values.code}
            error={errors.code}
            onChange={(e) => {
              set('code', e.target.value)
            }}
          />
          <Field
            ref={nameRef}
            label="Name"
            required
            maxLength={100}
            value={values.name}
            error={errors.name}
            onChange={(e) => {
              set('name', e.target.value)
            }}
          />
        </div>
        <div className="grid gap-4 sm:grid-cols-[1fr_2fr]">
          <Field
            ref={guestsRef}
            label="Max guests"
            required
            type="number"
            min={1}
            max={MAX_GUESTS}
            value={values.max_occupancy}
            error={errors.max_occupancy}
            onChange={(e) => {
              set('max_occupancy', e.target.value)
            }}
          />
          <Field
            label="Beds"
            maxLength={100}
            hint="e.g. 1 king, or 2 singles"
            value={values.bed_setup}
            onChange={(e) => {
              set('bed_setup', e.target.value)
            }}
          />
        </div>
        <Field
          ref={rateRef}
          label={`Price per night (${currency})`}
          required
          inputMode="decimal"
          hint="The default price of a night; bookings already made keep theirs."
          value={values.rate}
          error={errors.rate}
          onChange={(e) => {
            set('rate', e.target.value)
          }}
        />
        <TextAreaField
          label="Description"
          maxLength={2000}
          value={values.description}
          onChange={(e) => {
            set('description', e.target.value)
          }}
        />
        <div className="flex justify-end gap-3">
          <button type="button" onClick={onClose} className={buttonSecondary}>
            Cancel
          </button>
          <button type="submit" disabled={saving.isPending} className={buttonPrimary}>
            {saving.isPending ? 'Saving…' : roomType ? 'Save room type' : 'Add room type'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}
