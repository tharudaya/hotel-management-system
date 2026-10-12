import { Star, StickyNote } from 'lucide-react'
import { memo } from 'react'

import type { DashboardRoom } from '../api/types'
import { focusRing } from '../components/styles'
import { HOUSEKEEPING, OCCUPANCY, roomAccessibleLabel } from './status'

interface RoomTileProps {
  room: DashboardRoom
  selected: boolean
  onSelect: (roomId: number) => void
}

/**
 * Memoised: a realtime batch replaces only the changed room objects, so the other tiles
 * skip re-rendering (requires a stable `onSelect`).
 */
export const RoomTile = memo(function RoomTile({ room, selected, onSelect }: RoomTileProps) {
  const occupancy = OCCUPANCY[room.occupancy_status]
  const housekeeping = HOUSEKEEPING[room.housekeeping_status]
  const OccupancyIcon = occupancy.icon
  const HousekeepingIcon = housekeeping.icon

  return (
    <button
      type="button"
      data-room-id={room.room_id}
      aria-label={roomAccessibleLabel(room)}
      aria-current={selected ? 'true' : undefined}
      onClick={() => {
        onSelect(room.room_id)
      }}
      className={`flex h-full w-full flex-col gap-1.5 rounded-lg border-2 p-3 text-left transition-shadow hover:shadow-md ${occupancy.tile} ${selected ? 'ring-2 ring-sky-700 ring-offset-2' : ''} ${focusRing}`}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-xl leading-none font-bold text-slate-900">{room.number}</span>
        <span className="flex items-center gap-1">
          {room.has_notes && <StickyNote aria-hidden className="size-4 text-slate-600" />}
          {room.is_vip && (
            <span className="inline-flex items-center gap-0.5 rounded bg-amber-400 px-1.5 py-0.5 text-xs font-bold text-amber-950">
              <Star aria-hidden className="size-3 fill-current" />
              VIP
            </span>
          )}
        </span>
      </div>

      <span className="text-xs font-medium tracking-wide text-slate-600 uppercase">
        {room.room_type}
      </span>

      <span
        className={`inline-flex w-fit items-center gap-1 rounded px-1.5 py-0.5 text-xs font-semibold ${occupancy.chip}`}
      >
        <OccupancyIcon aria-hidden className="size-3.5" />
        {occupancy.label}
        {room.has_arrival_today && room.occupancy_status !== 'arriving_today' && ' · arrival'}
      </span>

      <span className="min-h-5 truncate text-sm text-slate-800">{room.guest_name ?? ' '}</span>

      {/* An out-of-order room already says so above; don't repeat it. */}
      {!(
        room.occupancy_status === 'out_of_order' && room.housekeeping_status === 'out_of_order'
      ) && (
        <span className="mt-auto inline-flex items-center gap-1 text-xs text-slate-700">
          <HousekeepingIcon aria-hidden className="size-3.5" />
          {housekeeping.label}
        </span>
      )}
    </button>
  )
})
