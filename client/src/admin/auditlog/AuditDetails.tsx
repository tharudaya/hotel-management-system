import type { AuditEntry } from '../../api/types'
import { useAuthenticatedUser } from '../../auth/currentUser'
import { formatDateTime } from '../../lib/format'
import { FIELD_LABELS } from '../audit'

const ISO_INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** A stored value as people read it. */
function readable(value: unknown, timeZone: string): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string') {
    return ISO_INSTANT.test(value) ? formatDateTime(value, timeZone) : value
  }
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') return String(value)
  if (Array.isArray(value))
    return value.length > 0 ? value.map((v) => readable(v, timeZone)).join(', ') : 'none'
  if (isRecord(value) && typeof value.name === 'string') return value.name
  return JSON.stringify(value)
}

function label(field: string): string {
  return FIELD_LABELS[field] ?? field.replaceAll('_', ' ')
}

/** Expanded row: before/after per field, permission changes, sign-in details. */
export function AuditDetails({ entry }: { entry: AuditEntry }) {
  const { property } = useAuthenticatedUser()
  const before = entry.before ?? {}
  const after = entry.after ?? {}
  // Permission changes are recorded as {added, removed}; show them as two lists.
  const permissions = isRecord(after.permissions)
    ? after.permissions
    : isRecord(before.permissions)
      ? before.permissions
      : null
  const fields = [...new Set([...Object.keys(before), ...Object.keys(after)])].filter(
    (field) => field !== 'permissions',
  )
  const details = Object.entries(entry.details ?? {})

  return (
    <div className="space-y-3 text-sm">
      {fields.length > 0 && (
        <table className="w-full max-w-2xl text-left">
          <caption className="sr-only">Changed fields</caption>
          <thead className="text-slate-600">
            <tr>
              <th scope="col" className="py-1 pr-4 font-medium">
                Field
              </th>
              <th scope="col" className="py-1 pr-4 font-medium">
                Before
              </th>
              <th scope="col" className="py-1 font-medium">
                After
              </th>
            </tr>
          </thead>
          <tbody>
            {fields.map((field) => (
              <tr key={field}>
                <th scope="row" className="py-1 pr-4 font-medium text-slate-700">
                  {label(field)}
                </th>
                <td className="py-1 pr-4 text-slate-600">
                  {field in before ? (
                    <del className="decoration-red-400">
                      {readable(before[field], property.time_zone)}
                    </del>
                  ) : (
                    '—'
                  )}
                </td>
                <td className="py-1 text-slate-900">
                  {field in after ? (
                    <ins className="no-underline">{readable(after[field], property.time_zone)}</ins>
                  ) : (
                    '—'
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {permissions && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
          <dt className="font-medium text-emerald-800">Permissions added</dt>
          <dd>{readable(permissions.added, property.time_zone)}</dd>
          <dt className="font-medium text-red-800">Permissions removed</dt>
          <dd>{readable(permissions.removed, property.time_zone)}</dd>
        </dl>
      )}
      {details.length > 0 && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
          {details.map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="font-medium text-slate-700">{label(key)}</dt>
              <dd className="text-slate-900">
                {typeof value === 'string'
                  ? value.replaceAll('_', ' ')
                  : readable(value, property.time_zone)}
              </dd>
            </div>
          ))}
        </dl>
      )}
      <p className="text-xs text-slate-500">
        {entry.action}
        {entry.ip ? ` · from ${entry.ip}` : ''}
      </p>
      {fields.length === 0 && !permissions && details.length === 0 && (
        <p className="text-slate-600">No further details were recorded.</p>
      )}
    </div>
  )
}
