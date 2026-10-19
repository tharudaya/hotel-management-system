/**
 * Plain-language wording for audit entries (server: models/audit.AuditAction and
 * models/auth.AuditEventType). ACTIONS is keyed by the generated action union, so a
 * new action on the server fails the type check until it gets a label here.
 */
import type { AuditActionName, AuditEntry } from '../api/types'

export type ActionGroup =
  'Sign-in' | 'Users' | 'Roles' | 'Room setup' | 'Front desk' | 'Reservations'

export const ACTIONS: Record<AuditActionName, { label: string; group: ActionGroup }> = {
  'auth.login_succeeded': { label: 'Signed in', group: 'Sign-in' },
  'auth.login_failed': { label: 'Sign-in failed', group: 'Sign-in' },
  'auth.login_blocked': { label: 'Sign-in blocked (too many attempts)', group: 'Sign-in' },
  'auth.logout': { label: 'Signed out', group: 'Sign-in' },
  'auth.password_reset_requested': { label: 'Asked for a password reset', group: 'Sign-in' },
  'auth.link_rejected': { label: 'Invalid invite or reset link used', group: 'Sign-in' },
  'user.created': { label: 'User created', group: 'Users' },
  'user.updated': { label: 'Profile changed', group: 'Users' },
  'user.role_changed': { label: 'Role changed', group: 'Users' },
  'user.deactivated': { label: 'User deactivated', group: 'Users' },
  'user.reactivated': { label: 'User reactivated', group: 'Users' },
  'user.sessions_revoked': { label: 'Signed out everywhere', group: 'Users' },
  'user.invite_sent': { label: 'Invite sent', group: 'Users' },
  'user.password_reset_sent': { label: 'Password reset sent', group: 'Users' },
  'user.activated': { label: 'Account activated', group: 'Users' },
  'user.password_reset': { label: 'Password reset completed', group: 'Users' },
  'user.expired': { label: 'Access expired (sessions ended)', group: 'Users' },
  'role.created': { label: 'Role created', group: 'Roles' },
  'role.updated': { label: 'Role changed', group: 'Roles' },
  'role.deleted': { label: 'Role deleted', group: 'Roles' },
  'reservation.created': { label: 'Reservation made', group: 'Reservations' },
  'reservation.updated': { label: 'Reservation changed', group: 'Reservations' },
  'reservation.cancelled': { label: 'Reservation cancelled', group: 'Reservations' },
  'reservation.marked_no_show': { label: 'Marked as no-show', group: 'Reservations' },
  'reservation.room_assigned': { label: 'Room assigned', group: 'Front desk' },
  'reservation.room_unassigned': { label: 'Room unassigned', group: 'Front desk' },
  'guest.created': { label: 'Guest added', group: 'Reservations' },
  'guest.updated': { label: 'Guest details changed', group: 'Reservations' },
  'reservation.checked_in': { label: 'Checked in', group: 'Front desk' },
  'reservation.checked_out': { label: 'Checked out', group: 'Front desk' },
  'room.housekeeping_status_changed': { label: 'Housekeeping status changed', group: 'Front desk' },
  'room.created': { label: 'Room added', group: 'Room setup' },
  'room.updated': { label: 'Room changed', group: 'Room setup' },
  'room.retired': { label: 'Room retired', group: 'Room setup' },
  'room.reactivated': { label: 'Room reactivated', group: 'Room setup' },
  'room_type.created': { label: 'Room type added', group: 'Room setup' },
  'room_type.updated': { label: 'Room type changed', group: 'Room setup' },
  'room_type.retired': { label: 'Room type retired', group: 'Room setup' },
  'room_type.reactivated': { label: 'Room type reactivated', group: 'Room setup' },
}

export const ACTION_NAMES = Object.keys(ACTIONS) as AuditActionName[]

export function isActionName(value: string): value is AuditActionName {
  return value in ACTIONS
}

export function actionLabel(action: string): string {
  return isActionName(action) ? ACTIONS[action].label : action
}

export const FIELD_LABELS: Record<string, string> = {
  username: 'Username',
  full_name: 'Name',
  email: 'Email',
  phone: 'Phone',
  job_title: 'Job title',
  employee_id: 'Employee ID',
  expires_at: 'Access expires',
  role_id: 'Role ID',
  role_name: 'Role',
  number: 'Room number',
  floor: 'Floor',
  room_type_id: 'Room type ID',
  room_type_code: 'Room type',
  notes: 'Notes',
  code: 'Code',
  max_occupancy: 'Max guests',
  bed_setup: 'Beds',
  is_active: 'Active',
  retired_at: 'Retired',
  housekeeping_status: 'Housekeeping',
  status: 'Status',
  deactivated_at: 'Deactivated',
  sessions_revoked: 'Sessions ended',
  channel: 'Delivered by',
  name: 'Name',
  description: 'Description',
  permissions: 'Permissions',
  copied_from: 'Copied from',
  reason: 'Reason',
  outcome: 'Outcome',
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function count(value: unknown): number {
  return typeof value === 'number' ? value : 0
}

/** One sentence for an entry (used in a user's "Recent changes"). */
export function describeAuditEntry(entry: AuditEntry): string {
  const before = entry.before ?? {}
  const after = entry.after ?? {}
  switch (entry.action) {
    case 'user.created': {
      const role = text(after.role_name)
      return role ? `Created the account with the role ${role}` : 'Created the account'
    }
    case 'user.updated': {
      const fields = Object.keys(after).map((f) => (FIELD_LABELS[f] ?? f).toLowerCase())
      return fields.length > 0 ? `Changed ${fields.join(', ')}` : 'Changed the profile'
    }
    case 'user.role_changed': {
      const from = text(before.role_name)
      const to = text(after.role_name) ?? 'another role'
      return from ? `Changed the role from ${from} to ${to}` : `Gave the role ${to}`
    }
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
    case 'auth.login_failed': {
      const reason = text(entry.details?.reason)
      return reason ? `Sign-in failed (${reason.replaceAll('_', ' ')})` : 'Sign-in failed'
    }
    default:
      return actionLabel(entry.action)
  }
}
