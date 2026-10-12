/** Plain-language descriptions of audit log entries (see server models/audit.AuditAction). */
import type { AuditEntry } from '../api/types'

const FIELD_LABELS: Record<string, string> = {
  full_name: 'name',
  email: 'email',
  phone: 'phone',
  job_title: 'job title',
  employee_id: 'employee ID',
  expires_at: 'access expiry',
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function count(value: unknown): number {
  return typeof value === 'number' ? value : 0
}

export function describeAuditEntry(entry: AuditEntry): string {
  const before = entry.before ?? {}
  const after = entry.after ?? {}
  switch (entry.action) {
    case 'user.created': {
      const role = text(after.role_name)
      return role ? `Created the account with the role ${role}` : 'Created the account'
    }
    case 'user.updated': {
      const fields = Object.keys(after).map((field) => FIELD_LABELS[field] ?? field)
      return fields.length > 0 ? `Changed ${fields.join(', ')}` : 'Changed the profile'
    }
    case 'user.role_changed': {
      const from = text(before.role_name)
      const to = text(after.role_name) ?? 'another role'
      return from ? `Changed the role from ${from} to ${to}` : `Gave the role ${to}`
    }
    case 'user.deactivated':
      return 'Deactivated the account'
    case 'user.reactivated':
      return 'Reactivated the account'
    case 'user.sessions_revoked': {
      const n = count(after.sessions_revoked)
      return `Signed the user out everywhere (${n} ${n === 1 ? 'session' : 'sessions'})`
    }
    case 'user.invite_sent':
      return after.channel === 'email' ? 'Sent an invite by email' : 'Created an invite link'
    case 'user.password_reset_sent':
      return after.channel === 'email'
        ? 'Sent a password reset email'
        : 'Created a password reset link'
    case 'user.activated':
      return 'Activated the account'
    case 'user.password_reset':
      return 'Set a new password with a reset link'
    default:
      return entry.action
  }
}
