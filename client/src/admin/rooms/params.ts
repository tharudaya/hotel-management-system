/** Room list filters live in the URL: ?floor=3&type=2&status=retired. */

export type RoomStatusFilter = 'active' | 'retired' | 'all'
export const STATUS_FILTERS: readonly RoomStatusFilter[] = ['active', 'retired', 'all']
export const STATUS_FILTER_LABELS: Record<RoomStatusFilter, string> = {
  active: 'Active',
  retired: 'Retired',
  all: 'Active and retired',
}

export interface RoomFilters {
  floor: number | null
  typeId: number | null
  status: RoomStatusFilter
}

export const NO_FILTERS: RoomFilters = { floor: null, typeId: null, status: 'active' }

function nonNegativeInt(value: string | null): number | null {
  return value !== null && /^\d{1,3}$/.test(value) ? Number(value) : null
}

export function parseFilters(search: URLSearchParams): RoomFilters {
  const status = search.get('status')
  const typeId = nonNegativeInt(search.get('type'))
  return {
    floor: nonNegativeInt(search.get('floor')),
    typeId: typeId && typeId > 0 ? typeId : null,
    status: STATUS_FILTERS.find((s) => s === status) ?? 'active',
  }
}

export function toSearch(filters: RoomFilters): URLSearchParams {
  const search = new URLSearchParams()
  if (filters.floor !== null) search.set('floor', String(filters.floor))
  if (filters.typeId !== null) search.set('type', String(filters.typeId))
  if (filters.status !== 'active') search.set('status', filters.status)
  return search
}

export function toApiQuery(filters: RoomFilters): string {
  const query = new URLSearchParams({ status: filters.status })
  if (filters.floor !== null) query.set('floor', String(filters.floor))
  if (filters.typeId !== null) query.set('room_type_id', String(filters.typeId))
  return query.toString()
}

export function hasFilters(filters: RoomFilters): boolean {
  return filters.floor !== null || filters.typeId !== null || filters.status !== 'active'
}
