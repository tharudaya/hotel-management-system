import type { HousekeepingStatus, User } from '../api/types'
import { can, P } from '../auth/permissions'

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

/**
 * Statuses this user may move a room to: the transitions table, minus anything into or
 * out of "out of order" unless they may take rooms out of service.
 */
export function allowedHousekeepingTargets(
  current: HousekeepingStatus,
  user: User,
): HousekeepingStatus[] {
  const outOfService = can(user, P.ROOM_SET_OUT_OF_ORDER)
  return HOUSEKEEPING_TRANSITIONS[current].filter(
    (target) => outOfService || (current !== 'out_of_order' && target !== 'out_of_order'),
  )
}
