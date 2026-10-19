import { QueryClient } from '@tanstack/react-query'
import { beforeEach, describe, expect, it } from 'vitest'

import type { DashboardRoom, DashboardRooms, DashboardSummary } from '../api/types'
import { dashboardKeys } from '../dashboard/queries'
import { BUSINESS_DATE, fixtureRoom, ROOMS, SUMMARY } from '../test/fixtures'
import { applyEvents } from './cache'
import { parseServerMessage } from './protocol'
import type { RealtimeEvent } from './protocol'

let queryClient: QueryClient

beforeEach(() => {
  queryClient = new QueryClient()
  queryClient.setQueryData<DashboardRooms>(dashboardKeys.rooms, {
    business_date: BUSINESS_DATE,
    rooms: ROOMS,
  })
  queryClient.setQueryData<DashboardSummary>(dashboardKeys.summary, SUMMARY)
})

function roomEvent(room: DashboardRoom, seq = 1, businessDate = BUSINESS_DATE): RealtimeEvent {
  return { type: 'room_status_changed', seq, payload: { business_date: businessDate, room } }
}

function cachedRooms(): DashboardRoom[] {
  return queryClient.getQueryData<DashboardRooms>(dashboardKeys.rooms)?.rooms ?? []
}

describe('applyEvents', () => {
  it('replaces only the changed room; the others keep their identity', () => {
    const updated = { ...fixtureRoom(4), occupancy_status: 'vacant' as const, version: 2 }

    applyEvents(queryClient, [roomEvent(updated)])

    const rooms = cachedRooms()
    // Query's structural sharing stores an equal copy of a changed room.
    expect(rooms.find((r) => r.room_id === 4)).toEqual(updated)
    for (const room of rooms.filter((r) => r.room_id !== 4)) {
      expect(ROOMS).toContain(room) // same object, so its tile skips re-rendering
    }
  })

  it('applies the last update per room within a batch', () => {
    const first = { ...fixtureRoom(1), housekeeping_status: 'dirty' as const, version: 2 }
    const second = { ...fixtureRoom(1), housekeeping_status: 'in_progress' as const, version: 3 }

    applyEvents(queryClient, [roomEvent(first, 1), roomEvent(second, 2)])

    expect(cachedRooms().find((r) => r.room_id === 1)).toEqual(second)
  })

  it('never replaces a room with an older version', () => {
    queryClient.setQueryData<DashboardRooms>(dashboardKeys.rooms, {
      business_date: BUSINESS_DATE,
      rooms: ROOMS.map((r) => (r.room_id === 1 ? { ...r, version: 5 } : r)),
    })
    const before = queryClient.getQueryData(dashboardKeys.rooms)

    applyEvents(queryClient, [roomEvent({ ...fixtureRoom(1), version: 4 })])

    expect(queryClient.getQueryData(dashboardKeys.rooms)).toBe(before)
  })

  it('in strict (replay) mode, equal versions do not overwrite the snapshot', () => {
    const before = queryClient.getQueryData(dashboardKeys.rooms)

    applyEvents(queryClient, [roomEvent({ ...fixtureRoom(1), guest_name: 'stale' })], {
      strict: true,
    })

    expect(queryClient.getQueryData(dashboardKeys.rooms)).toBe(before)
  })

  it('replaces the summary', () => {
    const summary = { ...SUMMARY, dirty: 9 }

    applyEvents(queryClient, [{ type: 'summary_changed', seq: 1, payload: { summary } }])

    expect(queryClient.getQueryData(dashboardKeys.summary)).toEqual(summary)
  })

  it('asks for a resync when events belong to a new business date', () => {
    const { needsResync } = applyEvents(queryClient, [
      roomEvent({ ...fixtureRoom(1), version: 2 }, 1, '2026-10-13'),
    ])

    expect(needsResync).toBe(true)
    expect(cachedRooms()).toEqual(ROOMS)
  })

  it('leaves the caches alone for reservation changes (no resync)', () => {
    const { needsResync } = applyEvents(queryClient, [
      { type: 'reservation_changed', seq: 1, payload: { property_id: 1, reservation_id: 42 } },
    ])

    expect(needsResync).toBe(false)
    expect(cachedRooms()).toBe(ROOMS)
  })

  it('does nothing before the snapshot has loaded', () => {
    const empty = new QueryClient()

    applyEvents(empty, [roomEvent({ ...fixtureRoom(1), version: 2 })])

    expect(empty.getQueryData(dashboardKeys.rooms)).toBeUndefined()
  })
})

describe('parseServerMessage: reservation_changed', () => {
  it('keeps the ids and drops everything else', () => {
    const message = parseServerMessage(
      JSON.stringify({
        type: 'reservation_changed',
        seq: 7,
        payload: { property_id: 1, reservation_id: 42, change: 'created', status: 'confirmed' },
      }),
    )

    expect(message).toEqual({
      type: 'reservation_changed',
      seq: 7,
      payload: { property_id: 1, reservation_id: 42 },
    })
  })

  it('rejects a malformed payload', () => {
    const text = JSON.stringify({ type: 'reservation_changed', seq: 7, payload: {} })

    expect(parseServerMessage(text)).toBeNull()
  })
})
