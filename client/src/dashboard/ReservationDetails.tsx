import { Star } from 'lucide-react'

import type { ReservationDetail } from '../api/types'
import { buttonSecondary } from '../components/styles'
import { formatDate, plural } from '../lib/format'
import { useReservation } from './queries'

export function ReservationDetails({ reservationId }: { reservationId: number }) {
  const { data, isPending, isError, refetch, isRefetching } = useReservation(reservationId)

  if (isPending) {
    return (
      <p role="status" className="text-sm text-slate-500">
        Loading reservation…
      </p>
    )
  }
  if (isError) {
    return (
      <div role="alert" className="space-y-2 text-sm text-red-700">
        <p>Could not load the reservation details.</p>
        <button
          type="button"
          disabled={isRefetching}
          onClick={() => {
            void refetch()
          }}
          className={buttonSecondary}
        >
          {isRefetching ? 'Retrying…' : 'Retry'}
        </button>
      </div>
    )
  }
  return <Details reservation={data} />
}

function Details({ reservation: r }: { reservation: ReservationDetail }) {
  const guests = [
    plural(r.adults, 'adult'),
    ...(r.children > 0 ? [plural(r.children, 'child', 'children')] : []),
  ].join(', ')

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <h3 className="text-lg font-semibold text-slate-900">
          {r.guest.first_name} {r.guest.last_name}
        </h3>
        {r.is_vip && (
          <span className="inline-flex items-center gap-0.5 rounded bg-amber-400 px-1.5 py-0.5 text-xs font-bold text-amber-950">
            <Star aria-hidden className="size-3 fill-current" />
            VIP
          </span>
        )}
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
        <dt className="text-slate-500">Confirmation</dt>
        <dd className="font-mono text-slate-900">{r.confirmation_code}</dd>
        <dt className="text-slate-500">Stay</dt>
        <dd className="text-slate-900">
          {formatDate(r.check_in_date)} – {formatDate(r.check_out_date)} (
          {plural(r.nights, 'night')})
        </dd>
        <dt className="text-slate-500">Guests</dt>
        <dd className="text-slate-900">{guests}</dd>
        <dt className="text-slate-500">Room type</dt>
        <dd className="text-slate-900">{r.room_type_name}</dd>
        <dt className="text-slate-500">Email</dt>
        <dd className="break-all text-slate-900">{r.guest.email ?? '—'}</dd>
        <dt className="text-slate-500">Phone</dt>
        <dd className="text-slate-900">{r.guest.phone ?? '—'}</dd>
      </dl>
      {r.notes && (
        <div className="rounded-md bg-yellow-50 px-3 py-2 text-sm text-yellow-900">
          <span className="font-medium">Notes: </span>
          {r.notes}
        </div>
      )}
    </div>
  )
}
