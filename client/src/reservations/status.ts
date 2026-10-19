/** How reservation statuses and sources read and look. Never colour alone: icon + text. */
import { BedDouble, CalendarCheck, CircleSlash, LogOut, type LucideIcon, UserX } from 'lucide-react'

import type { ReservationSource, ReservationStatus } from '../api/types'

export const RESERVATION_STATUSES: readonly ReservationStatus[] = [
  'confirmed',
  'checked_in',
  'checked_out',
  'cancelled',
  'no_show',
]

export const RESERVATION_STATUS: Record<
  ReservationStatus,
  { label: string; icon: LucideIcon; badge: string }
> = {
  confirmed: {
    label: 'Confirmed',
    icon: CalendarCheck,
    badge: 'bg-sky-50 text-sky-900 ring-sky-200',
  },
  checked_in: {
    label: 'Checked in',
    icon: BedDouble,
    badge: 'bg-emerald-50 text-emerald-900 ring-emerald-200',
  },
  checked_out: {
    label: 'Checked out',
    icon: LogOut,
    badge: 'bg-slate-100 text-slate-800 ring-slate-200',
  },
  cancelled: {
    label: 'Cancelled',
    icon: CircleSlash,
    badge: 'bg-red-50 text-red-900 ring-red-200',
  },
  no_show: {
    label: 'No-show',
    icon: UserX,
    badge: 'bg-amber-50 text-amber-900 ring-amber-200',
  },
}

export const SOURCE_LABEL: Record<ReservationSource, string> = {
  phone: 'Phone',
  email: 'Email',
  walk_in: 'Walk-in',
  other: 'Other',
}
