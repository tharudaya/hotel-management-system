/**
 * The users list's search, filters, sort and page live in the URL query string, so a
 * view can be bookmarked, shared, and restored with the back button.
 */
import type { UserStatus } from '../../api/types'

export const USER_STATUSES: readonly UserStatus[] = ['active', 'invited', 'deactivated', 'expired']

export const SORTABLE = ['name', 'username', 'email', 'last_login_at'] as const
export type SortField = (typeof SORTABLE)[number]
export type Sort = SortField | `-${SortField}`

export const PAGE_SIZE = 25

export interface UserListParams {
  q: string
  roleId: number | null
  status: UserStatus | null
  sort: Sort
  page: number
}

export const DEFAULT_PARAMS: UserListParams = {
  q: '',
  roleId: null,
  status: null,
  sort: 'name',
  page: 1,
}

function isStatus(value: string | null): value is UserStatus {
  return USER_STATUSES.some((status) => status === value)
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

/** Read the URL; anything invalid falls back to the default. */
export function parseParams(search: URLSearchParams): UserListParams {
  const status = search.get('status')
  const sort = search.get('sort')
  return {
    q: (search.get('q') ?? '').slice(0, 100),
    roleId: positiveInt(search.get('role')),
    status: isStatus(status) ? status : null,
    sort: isSort(sort) ? sort : DEFAULT_PARAMS.sort,
    page: positiveInt(search.get('page')) ?? 1,
  }
}

/** Write the URL; defaults are left out to keep it short. */
export function toSearch(params: UserListParams): URLSearchParams {
  const search = new URLSearchParams()
  if (params.q) search.set('q', params.q)
  if (params.roleId !== null) search.set('role', String(params.roleId))
  if (params.status) search.set('status', params.status)
  if (params.sort !== DEFAULT_PARAMS.sort) search.set('sort', params.sort)
  if (params.page !== 1) search.set('page', String(params.page))
  return search
}

/** The API's query string for GET /api/admin/users. */
export function toApiQuery(params: UserListParams): string {
  const query = new URLSearchParams({
    sort: params.sort,
    page: String(params.page),
    page_size: String(PAGE_SIZE),
  })
  if (params.q.trim()) query.set('q', params.q.trim())
  if (params.roleId !== null) query.set('role_id', String(params.roleId))
  if (params.status) query.set('status', params.status)
  return query.toString()
}

export function hasFilters(params: UserListParams): boolean {
  return params.q.trim() !== '' || params.roleId !== null || params.status !== null
}
