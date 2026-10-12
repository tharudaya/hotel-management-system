import type { UserStatus } from '../../api/types'
import { USER_STATUS } from './status'

export function StatusBadge({ status }: { status: UserStatus }) {
  const { label, icon: Icon, chip } = USER_STATUS[status]
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${chip}`}
    >
      <Icon aria-hidden="true" className="size-3.5" />
      {label}
    </span>
  )
}
