import { type ComponentProps, useId } from 'react'

import { fieldClass } from '../../components/styles'

/** A labelled input with an optional hint and error, wired up with aria-describedby. */
export function Field({
  label,
  hint,
  error,
  required,
  ...input
}: ComponentProps<'input'> & { label: string; hint?: string; error?: string | undefined }) {
  const id = useId()
  const hintId = useId()
  const errorId = useId()
  const describedBy = [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(' ')
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-medium text-slate-700">
        {label}
        {required ? '' : <span className="font-normal text-slate-500"> (optional)</span>}
      </label>
      <input
        id={id}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        className={`${fieldClass} aria-invalid:border-red-600`}
        {...input}
      />
      {error && (
        <p id={errorId} className="text-sm text-red-700">
          {error}
        </p>
      )}
      {hint && (
        <p id={hintId} className="text-xs text-slate-500">
          {hint}
        </p>
      )}
    </div>
  )
}
