/** The audit log's filters live in the URL query string (bookmarkable, shareable). */
import type { AuditActionName, AuditEntry } from '../../api/types'
import { isActionName } from '../audit'

export type TargetType = NonNullable<AuditEntry['target_type']>
export const TARGET_TYPES: readonly TargetType[] = ['user', 'role', 'reservation', 'room']
export const TARGET_LABELS: Record<TargetType, string> = {
  user: 'User',
  role: 'Role',
  reservation: 'Reservation',
  room: 'Room',
}

export const PAGE_SIZE = 50

export interface AuditFilters {
  actor: string
  action: AuditActionName | null
  targetType: TargetType | null
  targetId: string
  from: string
  to: string
}

export const NO_FILTERS: AuditFilters = {
  actor: '',
  action: null,
  targetType: null,
  targetId: '',
  from: '',
  to: '',
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

function isTargetType(value: string | null): value is TargetType {
  return TARGET_TYPES.some((t) => t === value)
}

export function parseFilters(search: URLSearchParams): AuditFilters {
  const action = search.get('action')
  const targetType = search.get('target_type')
  const date = (name: string) => {
    const value = search.get(name) ?? ''
    return ISO_DATE.test(value) ? value : ''
  }
  return {
    actor: (search.get('actor') ?? '').slice(0, 50),
    action: action && isActionName(action) ? action : null,
    targetType: isTargetType(targetType) ? targetType : null,
    targetId: (search.get('target_id') ?? '').slice(0, 64),
    from: date('from'),
    to: date('to'),
  }
}

export function toSearch(filters: AuditFilters): URLSearchParams {
  const search = new URLSearchParams()
  if (filters.actor) search.set('actor', filters.actor)
  if (filters.action) search.set('action', filters.action)
  if (filters.targetType) search.set('target_type', filters.targetType)
  if (filters.targetId) search.set('target_id', filters.targetId)
  if (filters.from) search.set('from', filters.from)
  if (filters.to) search.set('to', filters.to)
  return search
}

/** The query string for GET /api/admin/audit (without the cursor). */
export function toApiQuery(filters: AuditFilters): string {
  const query = new URLSearchParams({ limit: String(PAGE_SIZE) })
  if (filters.actor.trim()) query.set('actor', filters.actor.trim())
  if (filters.action) query.set('action', filters.action)
  if (filters.targetType) query.set('target_type', filters.targetType)
  if (filters.targetId.trim()) query.set('target_id', filters.targetId.trim())
  if (filters.from) query.set('date_from', filters.from)
  if (filters.to) query.set('date_to', filters.to)
  return query.toString()
}

export function hasFilters(filters: AuditFilters): boolean {
  return Object.values(filters).some((value) => value !== '' && value !== null)
}

/** A problem that would make the API refuse the filters, or null. */
export function filterProblem(filters: AuditFilters): string | null {
  if (filters.from && filters.to && filters.from > filters.to) {
    return 'The start date is after the end date.'
  }
  return null
}
