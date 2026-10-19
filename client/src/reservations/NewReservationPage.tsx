import { Link } from 'react-router'

import { focusRing } from '../components/styles'
import { useDocumentTitle } from '../lib/useDocumentTitle'

/** /reservations/new: the booking form comes in the next step; until then, say so. */
export function NewReservationPage() {
  useDocumentTitle('New reservation')
  return (
    <section aria-labelledby="new-reservation-heading" className="space-y-4">
      <Link
        to="/reservations"
        className={`inline-block rounded-sm text-sm text-sky-800 hover:underline ${focusRing}`}
      >
        ← All reservations
      </Link>
      <h1 id="new-reservation-heading" className="text-xl font-semibold text-slate-900">
        New reservation
      </h1>
      <p className="rounded-lg bg-white p-6 text-slate-700 shadow-sm">
        The booking form is not available in this version yet.
      </p>
    </section>
  )
}
