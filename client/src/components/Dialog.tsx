import { type ReactNode, useEffect, useId, useRef } from 'react'
import { createPortal } from 'react-dom'

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * Modal dialog: labelled by its title, traps Tab inside, closes on Escape, makes the
 * rest of the page inert while open, and returns focus to whatever had it before
 * opening (normally the button that opened it).
 * Render it only while open.
 */
export function Dialog({
  title,
  description,
  onClose,
  children,
  initialFocus,
}: {
  title: string
  description?: ReactNode
  onClose: () => void
  children: ReactNode
  /** Element to focus first (default: the first focusable element). */
  initialFocus?: React.RefObject<HTMLElement | null>
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const descriptionId = useId()
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  })

  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const panel = panelRef.current
    // Everything else on the page becomes inert (no focus, no clicks, hidden from screen
    // readers' browse mode); aria-modal alone is not honoured everywhere.
    const overlay = panel?.parentElement ?? null
    const silenced = [...document.body.children].filter(
      (el): el is HTMLElement =>
        el instanceof HTMLElement && el !== overlay && !el.hasAttribute('inert'),
    )
    for (const el of silenced) {
      el.setAttribute('inert', '')
      el.setAttribute('aria-hidden', 'true')
    }
    const first = initialFocus?.current ?? panel?.querySelector<HTMLElement>(FOCUSABLE) ?? panel
    first?.focus()
    return () => {
      for (const el of silenced) {
        el.removeAttribute('inert')
        el.removeAttribute('aria-hidden')
      }
      // Back to the trigger once the dialog is gone.
      opener?.focus()
    }
    // Run once per opening: the dialog is mounted only while open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.stopPropagation()
      onCloseRef.current()
      return
    }
    if (event.key !== 'Tab' || !panelRef.current) return
    const items = [...panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)]
    const first = items[0]
    const last = items.at(-1)
    if (!first || !last) return
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4">
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl focus:outline-none"
      >
        <h2 id={titleId} className="text-lg font-semibold text-slate-900">
          {title}
        </h2>
        {description && (
          <div id={descriptionId} className="mt-2 text-sm text-slate-700">
            {description}
          </div>
        )}
        <div className="mt-5">{children}</div>
      </div>
    </div>,
    document.body,
  )
}
