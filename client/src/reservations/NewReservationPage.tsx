import { Link, useLocation } from 'react-router'

import { BookingFlow } from '../booking/BookingFlow'
import { focusRing } from '../components/styles'
import { useDocumentTitle } from '../lib/useDocumentTitle'

function Page({ title, walkIn }: { title: string; walkIn: boolean }) {
  useDocumentTitle(title)
  // A new location (e.g. "New reservation" after a booking) starts a fresh flow, with a
  // fresh Idempotency-Key.
  const location = useLocation()
  return (
    <section aria-labelledby="booking-heading" className="space-y-4">
      <Link
        to={walkIn ? '/' : '/reservations'}
        className={`inline-block rounded-sm text-sm text-sky-800 hover:underline ${focusRing}`}
      >
        {walkIn ? '← Dashboard' : '← All reservations'}
      </Link>
      <h1 id="booking-heading" className="text-xl font-semibold text-slate-900">
        {title}
      </h1>
      <BookingFlow key={location.key} walkIn={walkIn} />
    </section>
  )
}

/** /reservations/new */
export function NewReservationPage() {
  return <Page title="New reservation" walkIn={false} />
}

/** /reservations/walk-in: book from today and check in at once. */
export function WalkInPage() {
  return <Page title="Walk-in" walkIn />
}
