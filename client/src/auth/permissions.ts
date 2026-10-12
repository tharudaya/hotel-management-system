import type { User } from '../api/types'

/**
 * Permission keys, mirroring the catalog on the server (server/app/core/permissions.py);
 * `/api/auth/me` returns the user's keys. Showing or hiding UI is a convenience only:
 * the server checks every request.
 */
export const P = {
  USER_VIEW: 'user.view',
  USER_MANAGE: 'user.manage',
  ROLE_VIEW: 'role.view',
  ROLE_MANAGE: 'role.manage',
  DASHBOARD_VIEW: 'dashboard.view',
  ROOM_UPDATE_HOUSEKEEPING_STATUS: 'room.update_housekeeping_status',
  ROOM_SET_OUT_OF_ORDER: 'room.set_out_of_order',
  RESERVATION_VIEW: 'reservation.view',
  RESERVATION_CREATE: 'reservation.create',
  RESERVATION_MODIFY: 'reservation.modify',
  RESERVATION_CANCEL: 'reservation.cancel',
  RESERVATION_CHECK_IN: 'reservation.check_in',
  RESERVATION_CHECK_OUT: 'reservation.check_out',
  GUEST_VIEW: 'guest.view',
  GUEST_VIEW_CONTACT: 'guest.view_contact',
  PAYMENT_VIEW: 'payment.view',
  PAYMENT_REFUND: 'payment.refund',
  REPORT_VIEW: 'report.view',
  AUDIT_VIEW: 'audit.view',
  SETTINGS_MANAGE: 'settings.manage',
} as const

export type PermissionKey = (typeof P)[keyof typeof P]

/** Any of these opens the admin area (and shows its link in the header). */
export const ADMIN_PERMISSIONS: readonly PermissionKey[] = [P.USER_VIEW, P.ROLE_VIEW, P.AUDIT_VIEW]

export function can(user: User | null | undefined, permission: PermissionKey): boolean {
  return user?.permissions.includes(permission) ?? false
}

export function canAny(
  user: User | null | undefined,
  ...permissions: readonly PermissionKey[]
): boolean {
  return permissions.some((permission) => can(user, permission))
}
