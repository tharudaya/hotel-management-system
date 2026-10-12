import { type ReactNode, useRef } from 'react'

import { FormAlert } from './AuthShell'
import { Dialog } from './Dialog'
import { buttonPrimary, buttonSecondary, focusRing } from './styles'

const buttonDanger =
  'inline-flex items-center justify-center gap-2 rounded-md bg-red-700 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-red-800 disabled:cursor-not-allowed disabled:opacity-60 ' +
  focusRing

/** Confirm-or-cancel dialog. Cancel has focus first; errors show inside the dialog. */
export function ConfirmDialog({
  title,
  description,
  confirmLabel,
  danger = false,
  pending,
  error,
  onConfirm,
  onCancel,
}: {
  title: string
  description: ReactNode
  confirmLabel: string
  danger?: boolean | undefined
  pending: boolean
  error: string | null
  onConfirm: () => void
  onCancel: () => void
}) {
  const cancelRef = useRef<HTMLButtonElement>(null)
  return (
    // Cancel gets focus first: Enter on a just-opened dialog should not destroy anything.
    <Dialog title={title} description={description} onClose={onCancel} initialFocus={cancelRef}>
      <div className="space-y-4">
        <FormAlert message={error} />
        <div className="flex justify-end gap-3">
          <button ref={cancelRef} type="button" onClick={onCancel} className={buttonSecondary}>
            Cancel
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={onConfirm}
            className={danger ? buttonDanger : buttonPrimary}
          >
            {pending ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </Dialog>
  )
}
