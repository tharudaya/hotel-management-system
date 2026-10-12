/**
 * How each room status looks and reads. Every status has a text label and an icon,
 * so color is never the only signal.
 */
import {
  BadgeCheck,
  Ban,
  BedDouble,
  DoorOpen,
  Loader,
  LogIn,
  LogOut,
  type LucideIcon,
  Sparkles,
  Trash2,
  Wrench,
} from 'lucide-react'

import type { DashboardRoom, HousekeepingStatus, OccupancyStatus } from '../api/types'

interface StatusStyle {
  label: string
  icon: LucideIcon
  /** Classes for the room tile background and border. */
  tile: string
  /** Classes for small badges and the status chip. */
  chip: string
}

export const OCCUPANCY: Record<OccupancyStatus, StatusStyle> = {
  vacant: {
    label: 'Vacant',
    icon: DoorOpen,
    tile: 'bg-emerald-50 border-emerald-300',
    chip: 'bg-emerald-100 text-emerald-900',
  },
  occupied: {
    label: 'Occupied',
    icon: BedDouble,
    tile: 'bg-sky-100 border-sky-300',
    chip: 'bg-sky-200 text-sky-950',
  },
  arriving_today: {
    label: 'Arriving',
    icon: LogIn,
    tile: 'bg-amber-50 border-amber-300',
    chip: 'bg-amber-100 text-amber-950',
  },
  departing_today: {
    label: 'Departing',
    icon: LogOut,
    tile: 'bg-violet-100 border-violet-300',
    chip: 'bg-violet-200 text-violet-950',
  },
  out_of_order: {
    label: 'Out of order',
    icon: Wrench,
    tile: 'bg-slate-200 border-slate-400',
    chip: 'bg-slate-300 text-slate-900',
  },
}

/**
 * Staff-facing names: "clean" is shown as Inspection (cleaned, awaiting inspection) and
 * "inspected" as Ready; both can be checked in. The server uses the same names in its
 * messages (HOUSEKEEPING_LABELS in server/app/services/rooms.py).
 *
 * Housekeeping labels reuse the occupancy palette (rose for dirty, which has no
 * occupancy counterpart). They are rounded, bordered pills so they can't be mistaken
 * for the square occupancy labels of the same color, and the border keeps them visible
 * on a tile of the same hue.
 */
export const HOUSEKEEPING: Record<
  HousekeepingStatus,
  {
    label: string
    icon: LucideIcon
    pill: string
    /** Button that moves a room to this status. */
    action: string
    /** Confirmation after moving a room to this status. */
    done: (roomNumber: string) => string
  }
> = {
  dirty: {
    label: 'Dirty',
    icon: Trash2,
    pill: 'bg-rose-100 text-rose-900 border-rose-300',
    action: 'Mark dirty',
    done: (n) => `Room ${n} marked dirty.`,
  },
  in_progress: {
    label: 'Cleaning',
    icon: Loader,
    pill: 'bg-amber-100 text-amber-950 border-amber-300',
    action: 'Start cleaning',
    done: (n) => `Cleaning started in room ${n}.`,
  },
  clean: {
    label: 'Inspection',
    icon: Sparkles,
    pill: 'bg-sky-200 text-sky-950 border-sky-400',
    action: 'Send to inspection',
    done: (n) => `Room ${n} sent to inspection.`,
  },
  inspected: {
    label: 'Ready',
    icon: BadgeCheck,
    pill: 'bg-emerald-100 text-emerald-900 border-emerald-300',
    action: 'Mark ready',
    done: (n) => `Room ${n} marked ready.`,
  },
  out_of_order: {
    label: 'Out of order',
    icon: Ban,
    pill: 'bg-slate-300 text-slate-900 border-slate-400',
    action: 'Mark out of order',
    done: (n) => `Room ${n} marked out of order.`,
  },
}

export const OCCUPANCY_ORDER: OccupancyStatus[] = [
  'vacant',
  'occupied',
  'arriving_today',
  'departing_today',
  'out_of_order',
]

export const HOUSEKEEPING_ORDER: HousekeepingStatus[] = [
  'dirty',
  'in_progress',
  'clean',
  'inspected',
  'out_of_order',
]

const OCCUPANCY_SPOKEN: Record<OccupancyStatus, string> = {
  vacant: 'vacant',
  occupied: 'occupied',
  arriving_today: 'arriving today',
  departing_today: 'occupied, departing today',
  out_of_order: 'out of order',
}

const HOUSEKEEPING_SPOKEN: Record<HousekeepingStatus, string> = {
  dirty: 'dirty',
  in_progress: 'cleaning in progress',
  clean: 'awaiting inspection',
  inspected: 'ready',
  out_of_order: 'out of order',
}

/** e.g. "Room 304, occupied, departing today, dirty, guest Ada Lovelace, VIP" */
export function roomAccessibleLabel(room: DashboardRoom): string {
  const parts = [`Room ${room.number}`, OCCUPANCY_SPOKEN[room.occupancy_status]]
  if (room.has_arrival_today && room.occupancy_status !== 'arriving_today') {
    parts.push('arrival due today')
  }
  const sameAsOccupancy =
    room.occupancy_status === 'out_of_order' && room.housekeeping_status === 'out_of_order'
  if (!sameAsOccupancy) parts.push(HOUSEKEEPING_SPOKEN[room.housekeeping_status])
  if (room.guest_name) parts.push(`guest ${room.guest_name}`)
  if (room.is_vip) parts.push('VIP')
  if (room.has_notes) parts.push('has notes')
  return parts.join(', ')
}
