/**
 * The reservation list's search, filters, sort and page live in the URL, so a view can
 * be bookmarked, shared and restored with the back button. Today's views ("Arriving
 * today", ...) are worked out by the server with the hotel's business date.
 */
import type { ReservationStatus } from '../api/types'
import type { SortValue } from '../components/table'
import { RESERVATION_STATUSES } from './status'

export const VIEWS = ['arriving_today', 'departing_today', 'in_house'] as const
export type View = (typeof VIEWS)[number]

export const VIEW_LABEL: Record<View, string> = {
  arriving_today: 'Arriving today',
  departing_today: 'Departing today',
  in_house: 'In house',
}

export const DATE_FIELDS = ['staying', 'arriving', 'departing'] as const
export type DateField = (typeof DATE_FIELDS)[number]

export const DATE_FIELD_LABEL: Record<DateField, string> = {
  staying: 'Staying',
  arriving: 'Arriving',
  departing: 'Departing',
}

export const SORTABLE = ['check_in_date', 'check_out_date', 'guest', 'confirmation_code'] as const
export type SortField = (typeof SORTABLE)[number]
export type Sort = SortValue<SortField>

export const PAGE_SIZE = 25

export interface ReservationListParams {
  q: string
  status: ReservationStatus | null
  view: View | null
  dates: DateField
  from: string
  to: string
  sort: Sort
  page: number
}

export const DEFAULT_PARAMS: ReservationListParams = {
  q: '',
  status: null,
  view: null,
  dates: 'staying',
  from: '',
  to: '',
  sort: 'check_in_date',
  page: 1,
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

function oneOf<T extends string>(values: readonly T[], value: string | null): T | null {
  return values.find((v) => v === value) ?? null
}

function isSort(value: string | null): value is Sort {
  if (value === null) return false
  const field = value.startsWith('-') ? value.slice(1) : value
  return SORTABLE.some((s) => s === field)
}

function positiveInt(value: string | null): number | null {
  if (value === null || !/^\d+$/.test(value)) return null
  const n = Number(value)
  return n >= 1 ? n : null
}

function isoDate(value: string | null): string {
  return value !== null && ISO_DATE.test(value) ? value : ''
}

/** Read the URL; anything invalid falls back to the default. */
export function parseParams(search: URLSearchParams): ReservationListParams {
  const sort = search.get('sort')
  return {
    q: (search.get('q') ?? '').slice(0, 100),
    status: oneOf(RESERVATION_STATUSES, search.get('status')),
    view: oneOf(VIEWS, search.get('view')),
    dates: oneOf(DATE_FIELDS, search.get('dates')) ?? 'staying',
    from: isoDate(search.get('from')),
    to: isoDate(search.get('to')),
    sort: isSort(sort) ? sort : DEFAULT_PARAMS.sort,
    page: positiveInt(search.get('page')) ?? 1,
  }
}

/** Write the URL; defaults are left out to keep it short. */
export function toSearch(params: ReservationListParams): URLSearchParams {
  const search = new URLSearchParams()
  if (params.q) search.set('q', params.q)
  if (params.status) search.set('status', params.status)
  if (params.view) search.set('view', params.view)
  // The choice is kept even before a date is picked (the API gets it only with dates).
  if (params.dates !== 'staying') search.set('dates', params.dates)
  if (params.from) search.set('from', params.from)
  if (params.to) search.set('to', params.to)
  if (params.sort !== DEFAULT_PARAMS.sort) search.set('sort', params.sort)
  if (params.page !== 1) search.set('page', String(params.page))
  return search
}

/** The query string for GET /api/reservations. */
export function toApiQuery(params: ReservationListParams): string {
  const query = new URLSearchParams({
    sort: params.sort,
    page: String(params.page),
    page_size: String(PAGE_SIZE),
  })
  if (params.q.trim()) query.set('q', params.q.trim())
  if (params.status) query.set('status', params.status)
  if (params.view) query.set('view', params.view)
  if (params.from || params.to) {
    query.set('dates', params.dates)
    // A range typed backwards is read the right way round.
    const [from, to] =
      params.from && params.to && params.from > params.to
        ? [params.to, params.from]
        : [params.from, params.to]
    if (from) query.set('date_from', from)
    if (to) query.set('date_to', to)
  }
  return query.toString()
}

export function hasFilters(params: ReservationListParams): boolean {
  return (
    params.q.trim() !== '' ||
    params.status !== null ||
    params.view !== null ||
    params.from !== '' ||
    params.to !== ''
  )
}

export const NO_FILTERS: Pick<ReservationListParams, 'q' | 'status' | 'view' | 'from' | 'to'> = {
  q: '',
  status: null,
  view: null,
  from: '',
  to: '',
}
