import { type ComponentProps, useId } from 'react'

import { fieldClass } from '../../components/styles'

/** A labelled, optional multi-line field with an optional hint. */
export function TextAreaField({
  label,
  hint,
  ...textarea
}: ComponentProps<'textarea'> & { label: string; hint?: string }) {
  const id = useId()
  const hintId = useId()
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-medium text-slate-700">
        {label} <span className="font-normal text-slate-500">(optional)</span>
      </label>
      <textarea
        id={id}
        rows={3}
        aria-describedby={hint ? hintId : undefined}
        className={fieldClass}
        {...textarea}
      />
      {hint && (
        <p id={hintId} className="text-xs text-slate-500">
          {hint}
        </p>
      )}
    </div>
  )
}
