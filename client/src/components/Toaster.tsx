import { useSyncExternalStore } from 'react'

import { focusRing } from './styles'
import { dismissToast, toastStore } from './toast'

/** Renders the toast store. The live region is always mounted so new toasts are read out. */
export function Toaster() {
  const toasts = useSyncExternalStore(toastStore.subscribe, toastStore.getSnapshot)
  return (
    <div
      role="status"
      aria-label="Notifications"
      className="pointer-events-none fixed inset-x-4 bottom-4 z-40 flex flex-col items-end gap-2 sm:left-auto"
    >
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className="pointer-events-auto flex max-w-sm items-start gap-3 rounded-lg border border-red-200 bg-white px-4 py-3 text-sm text-slate-800 shadow-lg"
        >
          <p className="flex-1">{toast.message}</p>
          <button
            type="button"
            onClick={() => {
              dismissToast(toast.id)
            }}
            className={`-m-1 rounded p-1 text-slate-500 hover:text-slate-800 ${focusRing}`}
          >
            <span aria-hidden="true">×</span>
            <span className="sr-only">Dismiss</span>
          </button>
        </div>
      ))}
    </div>
  )
}
