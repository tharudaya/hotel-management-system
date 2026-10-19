import { Star } from 'lucide-react'
import { Link } from 'react-router'

import type { ArrivalEntry } from '../api/types'
import { P } from '../auth/permissions'
import { usePermissions } from '../auth/usePermissions'
import { focusRing } from '../components/styles'
import { formatDate, plural } from '../lib/format'

/**
 * Today's arrivals from the summary (kept live by the realtime events): bookings still
 * waiting for a room first, then those with a room, then those already in.
 */
export function ArrivalsList({
  arrivals,
  businessDate,
}: {
  arrivals: ArrivalEntry[]
  businessDate: string
}) {
  const { can } = usePermissions()
  const linked = can(P.RESERVATION_VIEW)
  const waiting = arrivals.filter((a) => a.status === 'confirmed').length
  return (
    <section aria-labelledby="arrivals-heading" className="rounded-lg bg-white p-4 shadow-sm">
      <h2 id="arrivals-heading" className="text-base font-semibold text-slate-900">
        Arrivals today
        <span className="ml-2 text-sm font-normal text-slate-600">
          {arrivals.length === 0
            ? 'none'
            : `${plural(arrivals.length, 'booking')}, ${waiting} still to check in`}
        </span>
      </h2>
      {arrivals.length > 0 && (
        <ul className="mt-3 divide-y divide-slate-100 text-sm">
          {arrivals.map((a) => (
            <li key={a.reservation_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
              {linked ? (
                <Link
                  to={`/reservations/${a.reservation_id}`}
                  className={`rounded-sm font-medium text-sky-800 hover:underline ${focusRing}`}
                >
                  {a.guest_name}
                </Link>
              ) : (
                <span className="font-medium text-slate-900">{a.guest_name}</span>
              )}
              {a.is_vip && (
                <span className="inline-flex items-center gap-0.5 rounded bg-amber-400 px-1 text-xs font-bold text-amber-950">
                  <Star aria-hidden className="size-3 fill-current" />
                  VIP
                </span>
              )}
              <span className="font-mono text-slate-600">{a.confirmation_code}</span>
              <span className="text-slate-700">
                {a.room_type_name}, {plural(a.nights, 'night')}
              </span>
              {a.check_in_date < businessDate && (
                <span className="text-amber-900">Late: was due {formatDate(a.check_in_date)}</span>
              )}
              {a.status === 'checked_in' ? (
                <span className="text-emerald-800">Checked in to room {a.room_number}</span>
              ) : a.room_number ? (
                <span className="text-slate-700">Room {a.room_number}</span>
              ) : (
                <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-900">
                  No room yet
                </span>
              )}
              <span className="text-slate-500">until {formatDate(a.check_out_date)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
