import { Loader, type LucideIcon, Radio, WifiOff } from 'lucide-react'

import type { ConnectionStatus } from './connection'

const STATES: Record<ConnectionStatus, { label: string; icon: LucideIcon; className: string }> = {
  live: { label: 'Live', icon: Radio, className: 'bg-emerald-100 text-emerald-900' },
  connecting: { label: 'Connecting…', icon: Loader, className: 'bg-slate-100 text-slate-700' },
  reconnecting: {
    label: 'Reconnecting…',
    icon: Loader,
    className: 'bg-amber-100 text-amber-900',
  },
  offline: { label: 'Offline', icon: WifiOff, className: 'bg-red-100 text-red-900' },
}

/** Small realtime status chip for the header. Text + icon, not color alone. */
export function ConnectionIndicator({ status }: { status: ConnectionStatus }) {
  const { label, icon: Icon, className } = STATES[status]
  return (
    <p
      role="status"
      aria-label={`Live updates: ${label.replace('…', '')}`}
      title={
        status === 'live'
          ? 'Room changes appear automatically'
          : 'Room changes may be out of date until the connection is restored'
      }
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${className}`}
    >
      <Icon
        aria-hidden
        className={`size-3.5 ${status === 'live' ? '' : 'motion-safe:animate-spin'}`}
      />
      {label}
    </p>
  )
}
