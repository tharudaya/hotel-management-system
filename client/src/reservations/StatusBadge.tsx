import type { ReservationStatus } from '../api/types'
import { RESERVATION_STATUS } from './status'

export function ReservationStatusBadge({ status }: { status: ReservationStatus }) {
  const { label, icon: Icon, badge } = RESERVATION_STATUS[status]
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ring-1 ring-inset ${badge}`}
    >
      <Icon aria-hidden="true" className="size-3.5" />
      {label}
    </span>
  )
}
