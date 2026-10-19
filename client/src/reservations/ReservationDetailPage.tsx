import { Star } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link, useLocation, useParams } from 'react-router'

import { ApiError } from '../api/client'
import type { ReservationHistoryEntry, ReservationView } from '../api/types'
import { actionLabel } from '../admin/audit'
import { useAuthenticatedUser } from '../auth/currentUser'
import { buttonSecondary, focusRing } from '../components/styles'
import { formatDate, formatDateTime, formatMoney, plural } from '../lib/format'
import { useDocumentTitle } from '../lib/useDocumentTitle'
import { useReservationView } from './queries'
import { ReservationActions } from './ReservationActions'
import { RESERVATION_STATUS, SOURCE_LABEL } from './status'
import { ReservationStatusBadge } from './StatusBadge'

const sectionClass = 'space-y-3 rounded-lg bg-white p-6 shadow-sm'

/** /reservations/:reservationId */
export function ReservationDetailPage() {
  const { reservationId } = useParams()
  const location = useLocation()
  const id = Number(reservationId)
  const valid = Number.isInteger(id) && id > 0
  const query = useReservationView(valid ? id : 0)
  const r = query.data
  useDocumentTitle(
    r ? `${r.guest.first_name} ${r.guest.last_name} (${r.confirmation_code})` : 'Reservation',
  )

  const back = (
    <Link
      to={`/reservations${listSearchFrom(location.state)}`}
      className={`inline-block rounded-sm text-sm text-sky-800 hover:underline ${focusRing}`}
    >
      ← All reservations
    </Link>
  )

  if (!valid || (query.error instanceof ApiError && query.error.status === 404)) {
    return (
      <section className="space-y-4">
        {back}
        <h1 className="text-xl font-semibold text-slate-900">Reservation not found</h1>
        <p className="text-slate-700">There is no reservation with this ID.</p>
      </section>
    )
  }
  if (query.isPending) {
    return (
      <section className="space-y-4">
        {back}
        <p role="status" className="text-slate-600">
          Loading reservation…
        </p>
      </section>
    )
  }
  if (query.isError || !r) {
    return (
      <section className="space-y-4">
        {back}
        <div role="alert" className="space-y-3">
          <p className="text-slate-800">This reservation could not be loaded.</p>
          <button
            type="button"
            onClick={() => {
              void query.refetch()
            }}
            className={buttonSecondary}
          >
            Try again
          </button>
        </div>
      </section>
    )
  }

  const guestName = `${r.guest.first_name} ${r.guest.last_name}`
  return (
    <section aria-labelledby="reservation-heading" className="space-y-4">
      {back}
      <div className="flex flex-wrap items-center gap-3">
        <h1 id="reservation-heading" className="text-xl font-semibold text-slate-900">
          {guestName}
        </h1>
        <span className="font-mono text-slate-700">{r.confirmation_code}</span>
        <ReservationStatusBadge status={r.status} />
        {r.is_vip && <VipBadge />}
      </div>

      <ReservationActions reservation={r} />

      <div className="grid gap-4 lg:grid-cols-2">
        <GuestSection reservation={r} />
        <StaySection reservation={r} />
        <PriceSection reservation={r} />
        <div className="space-y-4">
          <RoomSection reservation={r} />
          <NotesSection reservation={r} />
        </div>
      </div>
      <HistorySection history={r.history} />
    </section>
  )
}

function listSearchFrom(state: unknown): string {
  if (typeof state === 'object' && state !== null && 'listSearch' in state) {
    const { listSearch } = state
    if (typeof listSearch === 'string' && listSearch.startsWith('?')) return listSearch
  }
  return ''
}

function VipBadge() {
  return (
    <span className="inline-flex items-center gap-0.5 rounded bg-amber-400 px-1.5 py-0.5 text-xs font-bold text-amber-950">
      <Star aria-hidden className="size-3 fill-current" />
      VIP
    </span>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = `section-${title.toLowerCase().replace(/\W+/g, '-')}`
  return (
    <section aria-labelledby={id} className={sectionClass}>
      <h2 id={id} className="text-lg font-semibold text-slate-900">
        {title}
      </h2>
      {children}
    </section>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-slate-900">{children}</dd>
    </>
  )
}

function None({ children = 'None' }: { children?: ReactNode }) {
  return <span className="text-slate-400">{children}</span>
}

function GuestSection({ reservation: r }: { reservation: ReservationView }) {
  return (
    <Section title="Guest">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
        <Row label="Name">
          {r.guest.first_name} {r.guest.last_name}
        </Row>
        {r.guest.contact_hidden ? (
          <Row label="Contact">
            <None>Hidden: your role does not include guests' contact details</None>
          </Row>
        ) : (
          <>
            <Row label="Email">
              {r.guest.email ? <span className="break-all">{r.guest.email}</span> : <None />}
            </Row>
            <Row label="Phone">{r.guest.phone ?? <None />}</Row>
          </>
        )}
      </dl>
    </Section>
  )
}

function StaySection({ reservation: r }: { reservation: ReservationView }) {
  const { property } = useAuthenticatedUser()
  const guests = [
    plural(r.adults, 'adult'),
    ...(r.children > 0 ? [plural(r.children, 'child', 'children')] : []),
  ].join(', ')
  return (
    <Section title="Stay">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
        <Row label="Arrival">
          <time dateTime={r.check_in_date}>{formatDate(r.check_in_date)}</time>
        </Row>
        <Row label="Departure">
          <time dateTime={r.check_out_date}>{formatDate(r.check_out_date)}</time>
        </Row>
        <Row label="Nights">{r.nights}</Row>
        <Row label="Guests">{guests}</Row>
        <Row label="Room type">{r.room_type_name}</Row>
        <Row label="Booked by">{SOURCE_LABEL[r.source]}</Row>
        <Row label="Booked on">
          <time dateTime={r.created_at}>{formatDateTime(r.created_at, property.time_zone)}</time>
        </Row>
      </dl>
    </Section>
  )
}

function PriceSection({ reservation: r }: { reservation: ReservationView }) {
  return (
    <Section title="Price">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Price per night</caption>
        <thead className="text-slate-500">
          <tr>
            <th scope="col" className="py-1 font-medium">
              Night
            </th>
            <th scope="col" className="py-1 text-right font-medium">
              Rate
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {r.nightly_rates.map((night) => (
            <tr key={night.night_date}>
              <td className="py-1 text-slate-800">
                <time dateTime={night.night_date}>{formatDate(night.night_date)}</time>
              </td>
              <td className="py-1 text-right text-slate-800 tabular-nums">
                {formatMoney(night.rate_minor, r.currency)}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t-2 border-slate-200">
            <th scope="row" className="py-2 font-semibold text-slate-900">
              Total
            </th>
            <td className="py-2 text-right font-semibold text-slate-900 tabular-nums">
              {formatMoney(r.total_minor, r.currency)}
            </td>
          </tr>
        </tfoot>
      </table>
    </Section>
  )
}

function RoomSection({ reservation: r }: { reservation: ReservationView }) {
  return (
    <Section title="Room">
      <p className="text-sm text-slate-800">
        {r.room_number ? (
          <>
            Room <strong>{r.room_number}</strong> ({r.room_type_name})
          </>
        ) : (
          <None>No room assigned yet</None>
        )}
      </p>
    </Section>
  )
}

function NotesSection({ reservation: r }: { reservation: ReservationView }) {
  return (
    <Section title="Notes">
      {r.notes ? (
        <p className="text-sm whitespace-pre-line text-slate-800">{r.notes}</p>
      ) : (
        <p className="text-sm">
          <None>No notes</None>
        </p>
      )}
    </Section>
  )
}

function HistorySection({ history }: { history: ReservationHistoryEntry[] }) {
  const { property } = useAuthenticatedUser()
  return (
    <Section title="History">
      {history.length === 0 ? (
        <p className="text-sm">
          <None>No changes recorded</None>
        </p>
      ) : (
        <ol className="space-y-2 text-sm">
          {history.map((entry) => (
            <li
              key={`${entry.occurred_at}-${entry.action}`}
              className="flex flex-wrap gap-x-3 gap-y-1"
            >
              <time dateTime={entry.occurred_at} className="text-slate-500 tabular-nums">
                {formatDateTime(entry.occurred_at, property.time_zone)}
              </time>
              <span className="font-medium text-slate-900">{actionLabel(entry.action)}</span>
              {entry.status_before &&
                entry.status_after &&
                entry.status_before !== entry.status_after && (
                  <span className="text-slate-700">
                    {RESERVATION_STATUS[entry.status_before].label} →{' '}
                    {RESERVATION_STATUS[entry.status_after].label}
                  </span>
                )}
              {entry.action === 'reservation.updated' && entry.changed.length > 0 && (
                <span className="text-slate-700">({entry.changed.map(fieldName).join(', ')})</span>
              )}
              <span className="text-slate-600">by {entry.actor?.full_name ?? 'the system'}</span>
              {entry.reason && (
                <span className="w-full text-slate-700">Reason: {entry.reason}</span>
              )}
            </li>
          ))}
        </ol>
      )}
    </Section>
  )
}

const FIELD_NAMES: Record<string, string> = {
  check_in_date: 'arrival',
  check_out_date: 'departure',
  room_type_id: 'room type',
  room_id: 'room',
  adults: 'adults',
  children: 'children',
  notes: 'notes',
  is_vip: 'VIP',
  nightly_rates: 'prices',
  total_minor: 'total',
}

function fieldName(field: string): string {
  return FIELD_NAMES[field] ?? field.replaceAll('_', ' ')
}
