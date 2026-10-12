import { X } from 'lucide-react'
import { useEffect, useRef } from 'react'

import type { DashboardRoom } from '../api/types'
import { buttonSecondary } from '../components/styles'
import { plural } from '../lib/format'
import { ReservationDetails } from './ReservationDetails'
import { RoomActions } from './RoomActions'
import { HOUSEKEEPING, OCCUPANCY } from './status'

interface RoomDetailPanelProps {
  room: DashboardRoom
  onClose: () => void
}

/**
 * Non-modal side panel: the grid stays usable. Focus moves to the panel title when it
 * opens (or switches room); Escape closes it.
 */
export function RoomDetailPanel({ room, onClose }: RoomDetailPanelProps) {
  const titleRef = useRef<HTMLHeadingElement>(null)
  const occupancy = OCCUPANCY[room.occupancy_status]
  const housekeeping = HOUSEKEEPING[room.housekeeping_status]
  const OccupancyIcon = occupancy.icon
  const HousekeepingIcon = housekeeping.icon

  useEffect(() => {
    titleRef.current?.focus()
  }, [room.room_id])

  return (
    <aside
      aria-labelledby="room-panel-title"
      onKeyDown={(e) => {
        if (e.key === 'Escape') onClose()
      }}
      className="fixed inset-y-0 right-0 z-20 flex w-full max-w-md flex-col border-l border-slate-200 bg-white shadow-xl"
    >
      <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4">
        <div>
          <h2
            id="room-panel-title"
            ref={titleRef}
            tabIndex={-1}
            className="text-xl font-semibold text-slate-900 focus:outline-none"
          >
            Room {room.number}
          </h2>
          <p className="text-sm text-slate-600">
            {room.room_type_name} · Floor {room.floor}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close room details"
          className={buttonSecondary}
        >
          <X aria-hidden className="size-4" />
        </button>
      </div>

      <div className="flex-1 space-y-6 overflow-y-auto px-5 py-4">
        <div className="flex flex-wrap gap-2 text-sm">
          <span
            className={`inline-flex items-center gap-1 rounded px-2 py-1 font-medium ${occupancy.chip}`}
          >
            <OccupancyIcon aria-hidden className="size-4" />
            {occupancy.label}
          </span>
          <span
            data-housekeeping-pill
            className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 font-medium ${housekeeping.pill}`}
          >
            <HousekeepingIcon aria-hidden className="size-4" />
            {housekeeping.label}
          </span>
          {room.nights_remaining !== null && (
            <span className="inline-flex items-center rounded bg-slate-100 px-2 py-1 text-slate-700">
              {room.occupancy_status === 'arriving_today'
                ? `${plural(room.nights_remaining, 'night')} stay`
                : room.nights_remaining === 0
                  ? 'Due out today'
                  : `${plural(room.nights_remaining, 'night')} left`}
            </span>
          )}
        </div>

        <section aria-label="Reservation">
          {room.reservation_id !== null ? (
            <ReservationDetails reservationId={room.reservation_id} />
          ) : (
            <p className="text-sm text-slate-600">
              {room.has_arrival_today
                ? 'An arrival is due today, but the room is out of order.'
                : 'No guest in this room.'}
            </p>
          )}
          {room.has_arrival_today && room.occupancy_status === 'departing_today' && (
            <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
              Another guest arrives today. Check this guest out and prepare the room.
            </p>
          )}
        </section>

        <section aria-label="Actions">
          <RoomActions key={room.room_id} room={room} />
        </section>
      </div>
    </aside>
  )
}
