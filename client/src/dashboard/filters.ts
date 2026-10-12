/**
 * Room grid filters. They live in the URL query string so a filtered view survives a
 * reload and can be shared; this module converts between the URL and a typed object
 * and applies the filters.
 */
import type { DashboardRoom, HousekeepingStatus, OccupancyStatus } from '../api/types'
import { HOUSEKEEPING_ORDER, OCCUPANCY_ORDER } from './status'

/** Quick views selected from the summary bar. */
export const QUICK_VIEWS = [
  'arrivals',
  'departures',
  'in_house',
  'available',
  'dirty',
  'out_of_order',
] as const
export type QuickView = (typeof QUICK_VIEWS)[number]

export interface RoomFilters {
  floor: number | null
  roomType: string | null
  occupancy: OccupancyStatus | null
  housekeeping: HousekeepingStatus | null
  search: string
  view: QuickView | null
}

export const NO_FILTERS: RoomFilters = {
  floor: null,
  roomType: null,
  occupancy: null,
  housekeeping: null,
  search: '',
  view: null,
}

const PARAM = {
  floor: 'floor',
  roomType: 'type',
  occupancy: 'occupancy',
  housekeeping: 'hk',
  search: 'q',
  view: 'view',
} as const satisfies Record<keyof RoomFilters, string>

function oneOf<T extends string>(value: string | null, allowed: readonly T[]): T | null {
  return allowed.find((option) => option === value) ?? null
}

/** Read filters from the URL, ignoring anything invalid. */
export function filtersFromParams(params: URLSearchParams): RoomFilters {
  const floor = Number.parseInt(params.get(PARAM.floor) ?? '', 10)
  const roomType = params.get(PARAM.roomType)
  return {
    floor: Number.isNaN(floor) ? null : floor,
    roomType: roomType === null || roomType === '' ? null : roomType,
    occupancy: oneOf(params.get(PARAM.occupancy), OCCUPANCY_ORDER),
    housekeeping: oneOf(params.get(PARAM.housekeeping), HOUSEKEEPING_ORDER),
    search: params.get(PARAM.search) ?? '',
    view: oneOf(params.get(PARAM.view), QUICK_VIEWS),
  }
}

/** Write filters into a copy of `base`, keeping unrelated params (e.g. the open room). */
export function filtersToParams(filters: RoomFilters, base: URLSearchParams): URLSearchParams {
  const params = new URLSearchParams(base)
  const values: Record<keyof RoomFilters, string | null> = {
    floor: filters.floor === null ? null : String(filters.floor),
    roomType: filters.roomType,
    occupancy: filters.occupancy,
    housekeeping: filters.housekeeping,
    search: filters.search.trim() === '' ? null : filters.search,
    view: filters.view,
  }
  for (const key of Object.keys(PARAM) as (keyof RoomFilters)[]) {
    const value = values[key]
    if (value === null) params.delete(PARAM[key])
    else params.set(PARAM[key], value)
  }
  return params
}

export function hasActiveFilters(filters: RoomFilters): boolean {
  return (
    filters.floor !== null ||
    filters.roomType !== null ||
    filters.occupancy !== null ||
    filters.housekeeping !== null ||
    filters.search.trim() !== '' ||
    filters.view !== null
  )
}

export function matchesView(room: DashboardRoom, view: QuickView): boolean {
  switch (view) {
    case 'arrivals':
      return room.has_arrival_today
    case 'departures':
      return room.occupancy_status === 'departing_today'
    case 'in_house':
      return room.occupancy_status === 'occupied' || room.occupancy_status === 'departing_today'
    case 'available':
      return room.occupancy_status === 'vacant' && !room.has_arrival_today
    case 'dirty':
      return room.housekeeping_status === 'dirty'
    case 'out_of_order':
      return room.housekeeping_status === 'out_of_order'
  }
}

function matchesSearch(room: DashboardRoom, search: string): boolean {
  const term = search.trim().toLowerCase()
  if (term === '') return true
  return (
    room.number.toLowerCase().includes(term) ||
    (room.guest_name?.toLowerCase().includes(term) ?? false)
  )
}

/** All active filters must match (AND). */
export function applyFilters(rooms: DashboardRoom[], filters: RoomFilters): DashboardRoom[] {
  return rooms.filter(
    (room) =>
      (filters.floor === null || room.floor === filters.floor) &&
      (filters.roomType === null || room.room_type === filters.roomType) &&
      (filters.occupancy === null || room.occupancy_status === filters.occupancy) &&
      (filters.housekeeping === null || room.housekeeping_status === filters.housekeeping) &&
      (filters.view === null || matchesView(room, filters.view)) &&
      matchesSearch(room, filters.search),
  )
}

/** Rooms grouped by floor, floors ascending, keeping the rooms' order within a floor. */
export function groupByFloor(rooms: DashboardRoom[]): { floor: number; rooms: DashboardRoom[] }[] {
  const floors = new Map<number, DashboardRoom[]>()
  for (const room of rooms) {
    const list = floors.get(room.floor)
    if (list) list.push(room)
    else floors.set(room.floor, [room])
  }
  return [...floors.entries()]
    .sort(([a], [b]) => a - b)
    .map(([floor, floorRooms]) => ({ floor, rooms: floorRooms }))
}
