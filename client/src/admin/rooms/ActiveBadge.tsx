import { Archive, CircleCheck } from 'lucide-react'

/** Active or Retired, with an icon and text (never color alone). */
export function ActiveBadge({ active }: { active: boolean }) {
  const { label, Icon, chip } = active
    ? { label: 'Active', Icon: CircleCheck, chip: 'bg-emerald-100 text-emerald-900' }
    : { label: 'Retired', Icon: Archive, chip: 'bg-slate-200 text-slate-800' }
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${chip}`}
    >
      <Icon aria-hidden="true" className="size-3.5" />
      {label}
    </span>
  )
}
