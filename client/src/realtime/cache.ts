/** Apply realtime events to the TanStack Query cache. */
import type { QueryClient } from '@tanstack/react-query'

import type { DashboardRoom, DashboardRooms, DashboardSummary } from '../api/types'
import { dashboardKeys } from '../dashboard/queries'
import type { RealtimeEvent } from './protocol'

export interface ApplyOptions {
  /**
   * Replace a cached room only if the event's version is strictly newer. Used when
   * replaying events over a fresh snapshot, which may already include them.
   */
  strict?: boolean
}

/**
 * Patch the rooms and summary caches in place of a refetch. Only changed rooms get new
 * objects, so unchanged tiles keep their props. Returns true if the events belong to a
 * different business date than the cached snapshot (midnight passed): the caller
 * should refetch instead.
 */
export function applyEvents(
  queryClient: QueryClient,
  events: RealtimeEvent[],
  { strict = false }: ApplyOptions = {},
): { needsResync: boolean } {
  let needsResync = false
  const latestRooms = new Map<number, { businessDate: string; room: DashboardRoom }>()
  let latestSummary: DashboardSummary | null = null

  for (const event of events) {
    if (event.type === 'room_status_changed') {
      latestRooms.set(event.payload.room.room_id, {
        businessDate: event.payload.business_date,
        room: event.payload.room,
      })
    } else {
      latestSummary = event.payload.summary
    }
  }

  if (latestRooms.size > 0) {
    queryClient.setQueryData<DashboardRooms>(dashboardKeys.rooms, (current) => {
      if (!current) return current // no snapshot yet; it will arrive with the changes
      const rooms = current.rooms.map((room) => {
        const update = latestRooms.get(room.room_id)
        if (!update) return room
        if (update.businessDate !== current.business_date) {
          needsResync = true
          return room
        }
        const newer = strict
          ? update.room.version > room.version
          : update.room.version >= room.version
        return newer ? update.room : room
      })
      // Same object when nothing changed, so nothing re-renders.
      const changed = rooms.some((room, i) => room !== current.rooms[i])
      return changed ? { ...current, rooms } : current
    })
  }

  if (latestSummary) {
    const summary = latestSummary
    queryClient.setQueryData<DashboardSummary>(dashboardKeys.summary, (current) => {
      if (current && current.business_date !== summary.business_date) {
        needsResync = true
        return current
      }
      return summary
    })
  }

  return { needsResync }
}
