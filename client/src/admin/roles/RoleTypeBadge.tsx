import { Lock, Shield, UserCog } from 'lucide-react'

/** System, Admin (system and immutable) or custom; icon + text, never color alone. */
export function RoleTypeBadge({ isSystem, isAdmin }: { isSystem: boolean; isAdmin: boolean }) {
  const { label, Icon, chip } = isAdmin
    ? { label: 'System, locked', Icon: Lock, chip: 'bg-slate-200 text-slate-800' }
    : isSystem
      ? { label: 'System', Icon: Shield, chip: 'bg-sky-100 text-sky-900' }
      : { label: 'Custom', Icon: UserCog, chip: 'bg-violet-100 text-violet-900' }
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${chip}`}
    >
      <Icon aria-hidden="true" className="size-3.5" />
      {label}
    </span>
  )
}
