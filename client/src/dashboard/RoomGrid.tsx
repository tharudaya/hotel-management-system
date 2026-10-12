import type { DashboardRoom } from '../api/types'
import { plural } from '../lib/format'
import { groupByFloor } from './filters'
import { RoomTile } from './RoomTile'

interface RoomGridProps {
  rooms: DashboardRoom[]
  selectedRoomId: number | null
  onSelect: (roomId: number) => void
}

export function RoomGrid({ rooms, selectedRoomId, onSelect }: RoomGridProps) {
  return (
    <div className="space-y-6">
      {groupByFloor(rooms).map(({ floor, rooms: floorRooms }) => (
        <section key={floor} aria-labelledby={`floor-${floor}`}>
          <h2 id={`floor-${floor}`} className="mb-2 text-sm font-semibold text-slate-700">
            Floor {floor}{' '}
            <span className="font-normal text-slate-500">
              ({plural(floorRooms.length, 'room')})
            </span>
          </h2>
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(9rem,1fr))] gap-3">
            {floorRooms.map((room) => (
              <li key={room.room_id}>
                <RoomTile
                  room={room}
                  selected={room.room_id === selectedRoomId}
                  onSelect={onSelect}
                />
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}
