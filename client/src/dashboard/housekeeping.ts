import type { HousekeepingStatus, UserRole } from '../api/types'

/**
 * Allowed housekeeping changes, mirroring HOUSEKEEPING_TRANSITIONS in
 * server/app/services/rooms.py. Used only to decide which buttons to show:
 * the server validates every change.
 */
export const HOUSEKEEPING_TRANSITIONS: Record<HousekeepingStatus, HousekeepingStatus[]> = {
  dirty: ['in_progress', 'clean', 'out_of_order'],
  in_progress: ['clean', 'dirty', 'out_of_order'],
  clean: ['inspected', 'dirty', 'out_of_order'],
  inspected: ['dirty', 'out_of_order'],
  out_of_order: ['dirty'],
}

/** Roles the server lets change housekeeping status (of those who can see the dashboard). */
export function canChangeHousekeeping(role: UserRole): boolean {
  return role === 'manager' || role === 'admin'
}
