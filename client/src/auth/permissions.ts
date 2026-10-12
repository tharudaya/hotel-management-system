import type { User } from '../api/types'

/**
 * Permission keys the client checks. The catalog lives on the server
 * (server/app/core/permissions.py); `/api/auth/me` returns the user's keys.
 * Showing or hiding UI is a convenience only: the server checks every request.
 */
export const P = {
  DASHBOARD_VIEW: 'dashboard.view',
  ROOM_UPDATE_HOUSEKEEPING_STATUS: 'room.update_housekeeping_status',
  ROOM_SET_OUT_OF_ORDER: 'room.set_out_of_order',
  RESERVATION_VIEW: 'reservation.view',
  RESERVATION_CHECK_IN: 'reservation.check_in',
  RESERVATION_CHECK_OUT: 'reservation.check_out',
} as const

export type PermissionKey = (typeof P)[keyof typeof P]

export function can(user: User, permission: PermissionKey): boolean {
  return user.permissions.includes(permission)
}
