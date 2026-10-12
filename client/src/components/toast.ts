/**
 * A tiny app-wide toast store (framework-free, read with useSyncExternalStore by
 * <Toaster>). Used for messages that don't belong to one form, e.g. a global 403.
 */

export interface Toast {
  id: number
  message: string
}

type Listener = () => void

const DEFAULT_TIMEOUT_MS = 8000

let toasts: readonly Toast[] = []
let nextId = 1
const listeners = new Set<Listener>()
const timers = new Map<number, ReturnType<typeof setTimeout>>()

function emit(next: readonly Toast[]): void {
  toasts = next
  for (const listener of listeners) listener()
}

export const toastStore = {
  subscribe: (listener: Listener): (() => void) => {
    listeners.add(listener)
    return () => listeners.delete(listener)
  },
  getSnapshot: (): readonly Toast[] => toasts,
}

/** Show a message; the same text already on screen is not repeated. */
export function showToast(message: string, { timeoutMs = DEFAULT_TIMEOUT_MS } = {}): void {
  if (toasts.some((toast) => toast.message === message)) return
  const id = nextId++
  emit([...toasts, { id, message }])
  timers.set(
    id,
    setTimeout(() => {
      dismissToast(id)
    }, timeoutMs),
  )
}

export function dismissToast(id: number): void {
  clearTimeout(timers.get(id))
  timers.delete(id)
  if (toasts.some((toast) => toast.id === id)) emit(toasts.filter((toast) => toast.id !== id))
}

/** Remove everything (tests, sign-out). */
export function clearToasts(): void {
  for (const timer of timers.values()) clearTimeout(timer)
  timers.clear()
  emit([])
}
