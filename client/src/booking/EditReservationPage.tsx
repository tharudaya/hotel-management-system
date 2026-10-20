/**
 * /reservations/:reservationId/edit: change a booking with the booking flow's building
 * blocks. Before saving, the server's preview (nothing saved) shows the new nights,
 * prices and the difference, with the same refusals saving would give.
 */
import { ArrowDown, ArrowUp, Minus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'

import { ApiError } from '../api/client'
import type { ReservationUpdate, ReservationView } from '../api/types'
import { P } from '../auth/permissions'
import { usePermissions } from '../auth/usePermissions'
import { FormAlert } from '../components/AuthShell'
import { buttonPrimary, buttonSecondary, focusRing } from '../components/styles'
import { useHotelInfo } from '../dashboard/queries'
import { formatDate, formatMoney, plural } from '../lib/format'
import { useDocumentTitle } from '../lib/useDocumentTitle'
import { useReservationView } from '../reservations/queries'
import {
  changeBody,
  type ChangeValues,
  hasErrors,
  rateError,
  type StayErrors,
  stayErrorFromServer,
  unavailableNights,
  validateStay,
} from './booking'
import { DetailsFields, RoomTypeChoice, StayFields } from './fields'
import { useAvailability, usePreviewChange, useSaveChange } from './queries'

export function EditReservationPage() {
  const { reservationId } = useParams()
  const id = Number(reservationId)
  const valid = Number.isInteger(id) && id > 0
  const query = useReservationView(valid ? id : 0)
  const today = useHotelInfo().data?.business_date
  const r = query.data
  useDocumentTitle(r ? `Change ${r.confirmation_code}` : 'Change reservation')

  const back = (
    <Link
      to={valid ? `/reservations/${id}` : '/reservations'}
      className={`inline-block rounded-sm text-sm text-sky-800 hover:underline ${focusRing}`}
    >
      ← Back to the reservation
    </Link>
  )
  if (!valid || (query.error instanceof ApiError && query.error.status === 404)) {
    return (
      <section className="space-y-4">
        {back}
        <h1 className="text-xl font-semibold text-slate-900">Reservation not found</h1>
      </section>
    )
  }
  if (!r || !today) {
    return (
      <section className="space-y-4">
        {back}
        <p role={query.isError ? 'alert' : 'status'} className="text-slate-600">
          {query.isError ? 'This reservation could not be loaded.' : 'Loading reservation…'}
        </p>
      </section>
    )
  }
  return (
    <section aria-labelledby="edit-heading" className="space-y-4">
      {back}
      <h1 id="edit-heading" className="text-xl font-semibold text-slate-900">
        Change {r.guest.first_name} {r.guest.last_name}'s booking{' '}
        <span className="font-mono font-normal text-slate-700">{r.confirmation_code}</span>
      </h1>
      {r.status === 'confirmed' || r.status === 'checked_in' ? (
        <EditForm reservation={r} today={today} />
      ) : (
        <p className="rounded-lg bg-white p-6 text-slate-700 shadow-sm">
          This booking is {r.status.replace('_', ' ')} and can no longer be changed.
        </p>
      )}
    </section>
  )
}

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

function EditForm({ reservation: r, today }: { reservation: ReservationView; today: string }) {
  const { can } = usePermissions()
  const navigate = useNavigate()
  const inHouse = r.status === 'checked_in'
  const [values, setValues] = useState<ChangeValues>({
    stay: {
      checkIn: r.check_in_date,
      checkOut: r.check_out_date,
      adults: String(r.adults),
      children: String(r.children),
    },
    roomTypeId: r.room_type_id,
    notes: r.notes ?? '',
    isVip: r.is_vip,
    rate: '',
  })
  const [stayErrors, setStayErrors] = useState<StayErrors>({})
  const [notice, setNotice] = useState<{ kind: 'conflict' | 'error'; text: string } | null>(null)
  const save = useSaveChange(r.id)

  // A guest in house keeps the arrival; a late arrival may keep an arrival in the past.
  const checkInFixed = inHouse || values.stay.checkIn === r.check_in_date
  const errors = validateStay(values.stay, today, { checkInFixed })
  const stayValid = !hasErrors(errors)
  const rateProblem = rateError(values.rate)
  const body = stayValid && !rateProblem ? changeBody(r, values) : null
  // Debounce by value: the body is a new object on every render.
  const bodyJson = JSON.stringify(body)
  const previewJson = useDebounced(bodyJson, 400)
  const previewBody = JSON.parse(previewJson) as ReservationUpdate | null
  const preview = usePreviewChange(r.id, previewBody)

  const canChangeType = !inHouse && can(P.RESERVATION_CREATE)
  const availability = useAvailability(
    canChangeType && stayValid && values.stay.checkIn >= today
      ? {
          checkIn: values.stay.checkIn,
          checkOut: values.stay.checkOut,
          adults: Number(values.stay.adults),
          children: Number(values.stay.children),
          excludeReservationId: r.id,
        }
      : null,
  )

  function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    setNotice(null)
    setStayErrors(errors)
    if (!stayValid || rateProblem) {
      setNotice({ kind: 'error', text: 'Check the highlighted fields.' })
      return
    }
    if (!body) {
      void navigate(`/reservations/${r.id}`)
      return
    }
    save.mutate(body, {
      onSuccess: () => {
        void navigate(`/reservations/${r.id}`, {
          state: { notice: `${r.confirmation_code} has been changed.` },
        })
      },
      onError: (error) => {
        if (error instanceof ApiError && error.code === 'version_conflict') {
          // The booking is refetched (useSaveChange); the edits here are kept.
          setNotice({
            kind: 'conflict',
            text: 'Someone else changed this booking while you were editing it. The latest version has been loaded and your changes are kept: check the preview and save again.',
          })
        } else if (error instanceof ApiError && error.status === 422) {
          setStayErrors(stayErrorFromServer(error))
          setNotice({ kind: 'error', text: error.message })
        } else {
          setNotice({
            kind: 'error',
            text:
              error instanceof ApiError && error.status < 500
                ? error.message
                : 'Saving failed. Try again.',
          })
        }
      },
    })
  }

  return (
    <form onSubmit={submit} noValidate className="grid gap-4 lg:grid-cols-[3fr_2fr]">
      <div className="space-y-4">
        {notice?.kind === 'conflict' ? (
          <div
            role="alert"
            className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
          >
            {notice.text}
          </div>
        ) : (
          <FormAlert message={notice?.text ?? null} />
        )}
        <section
          aria-labelledby="edit-stay"
          className="space-y-3 rounded-lg bg-white p-6 shadow-sm"
        >
          <h2 id="edit-stay" className="text-lg font-semibold text-slate-900">
            Dates and guests
          </h2>
          {inHouse && (
            <p className="text-sm text-slate-700">
              The guest is checked in: only the departure date and notes can change.
            </p>
          )}
          <StayFields
            value={values.stay}
            errors={stayErrors}
            today={today}
            checkInFixed={inHouse}
            guestsFixed={inHouse}
            onChange={(stay) => {
              setValues((v) => ({ ...v, stay }))
              setStayErrors({})
            }}
          />
        </section>
        {canChangeType && (
          <section
            aria-labelledby="edit-type"
            className="space-y-3 rounded-lg bg-white p-6 shadow-sm"
          >
            <h2 id="edit-type" className="text-lg font-semibold text-slate-900">
              Room type
            </h2>
            {availability.fetchStatus === 'idle' && !availability.data ? (
              <p className="text-sm text-slate-600">Choose valid dates to see the room types.</p>
            ) : (
              <RoomTypeChoice
                availability={availability}
                selected={values.roomTypeId}
                currentRoomTypeId={r.room_type_id}
                onSelect={(roomTypeId) => {
                  setValues((v) => ({ ...v, roomTypeId }))
                }}
              />
            )}
          </section>
        )}
        <section
          aria-labelledby="edit-details"
          className="space-y-3 rounded-lg bg-white p-6 shadow-sm"
        >
          <h2 id="edit-details" className="text-lg font-semibold text-slate-900">
            Details
          </h2>
          <DetailsFields
            value={{
              source: r.source,
              notes: values.notes,
              isVip: values.isVip,
              rate: values.rate,
            }}
            rateError={rateProblem}
            canOverrideRate={can(P.RESERVATION_OVERRIDE_RATE)}
            sourceFixed
            currency={r.currency}
            onChange={(d) => {
              setValues((v) => ({
                ...v,
                notes: d.notes,
                isVip: inHouse ? v.isVip : d.isVip,
                rate: d.rate,
              }))
            }}
          />
        </section>
      </div>

      <aside
        aria-labelledby="preview-heading"
        className="space-y-3 self-start rounded-lg bg-white p-6 shadow-sm"
      >
        <h2 id="preview-heading" className="text-lg font-semibold text-slate-900">
          Price before and after
        </h2>
        <Preview current={r} body={body} preview={preview} waiting={bodyJson !== previewJson} />
        <div className="flex flex-wrap gap-3 border-t border-slate-200 pt-4">
          <button
            type="submit"
            aria-disabled={save.isPending || undefined}
            className={buttonPrimary}
          >
            {save.isPending ? 'Saving…' : 'Save changes'}
          </button>
          <Link to={`/reservations/${r.id}`} className={buttonSecondary}>
            Cancel
          </Link>
        </div>
      </aside>
    </form>
  )
}

function Preview({
  current,
  body,
  preview,
  waiting,
}: {
  current: ReservationView
  body: ReservationUpdate | null
  preview: ReturnType<typeof usePreviewChange>
  waiting: boolean
}) {
  if (!body) {
    return (
      <p className="text-sm text-slate-600">
        No changes yet. Total {formatMoney(current.total_minor, current.currency)}.
      </p>
    )
  }
  if (waiting || preview.isPending) {
    return (
      <p role="status" className="text-sm text-slate-600">
        Working out the new price…
      </p>
    )
  }
  if (preview.error) {
    const nights = preview.error instanceof ApiError ? unavailableNights(preview.error) : []
    return (
      <p
        role="alert"
        className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
      >
        {preview.error instanceof ApiError && preview.error.status < 500
          ? preview.error.message
          : 'The new price could not be worked out.'}
        {nights.length > 0 && ` Full: ${nights.map(formatDate).join(', ')}.`} Saving would be
        refused.
      </p>
    )
  }
  const next = preview.data
  const difference = next.total_minor - current.total_minor
  const Icon = difference > 0 ? ArrowUp : difference < 0 ? ArrowDown : Minus
  return (
    <div role="status" className="space-y-3 text-sm">
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        <dt className="text-slate-500">Now</dt>
        <dd className="text-slate-900">
          {plural(current.nights, 'night')}, {formatMoney(current.total_minor, current.currency)}
        </dd>
        <dt className="text-slate-500">After</dt>
        <dd className="text-slate-900">
          {plural(next.nights, 'night')}, {formatMoney(next.total_minor, next.currency)}
        </dd>
        <dt className="text-slate-500">Difference</dt>
        <dd className="inline-flex items-center gap-1 font-semibold text-slate-900">
          <Icon aria-hidden="true" className="size-4" />
          {difference === 0
            ? 'No change'
            : `${difference > 0 ? 'Up' : 'Down'} ${formatMoney(Math.abs(difference), next.currency)}`}
        </dd>
      </dl>
      <table className="w-full text-left">
        <caption className="sr-only">New price per night</caption>
        <tbody className="divide-y divide-slate-100">
          {next.nightly_rates.map((n) => (
            <tr key={n.night_date}>
              <td className="py-1 text-slate-700">{formatDate(n.night_date)}</td>
              <td className="py-1 text-right text-slate-800 tabular-nums">
                {formatMoney(n.rate_minor, next.currency)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {current.room_number && next.room_id === null && (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-amber-900">
          Room {current.room_number} will be released: it is not free (or not of the right type) for
          the new stay.
        </p>
      )}
    </div>
  )
}
