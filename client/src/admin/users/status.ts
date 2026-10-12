import { Ban, CircleCheck, Clock, type LucideIcon, Mail } from 'lucide-react'

import type { UserStatus } from '../../api/types'

/** Every status has a text label and an icon, so color is never the only signal. */
export const USER_STATUS: Record<UserStatus, { label: string; icon: LucideIcon; chip: string }> = {
  active: { label: 'Active', icon: CircleCheck, chip: 'bg-emerald-100 text-emerald-900' },
  invited: { label: 'Invited', icon: Mail, chip: 'bg-sky-100 text-sky-900' },
  deactivated: { label: 'Deactivated', icon: Ban, chip: 'bg-slate-200 text-slate-800' },
  expired: { label: 'Expired', icon: Clock, chip: 'bg-amber-100 text-amber-950' },
}
