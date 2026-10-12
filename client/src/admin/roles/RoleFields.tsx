import { type Ref, useId } from 'react'

import { fieldClass } from '../../components/styles'
import { Field } from '../users/Field'

/** Name and description inputs for creating or editing a custom role. */
export function RoleFields({
  name,
  description,
  nameError,
  nameRef,
  onName,
  onDescription,
}: {
  name: string
  description: string
  nameError?: string | undefined
  nameRef?: Ref<HTMLInputElement>
  onName: (value: string) => void
  onDescription: (value: string) => void
}) {
  const descriptionId = useId()
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <Field
        ref={nameRef}
        label="Name"
        required
        maxLength={100}
        value={name}
        error={nameError}
        onChange={(e) => {
          onName(e.target.value)
        }}
      />
      <div className="space-y-1 sm:col-span-2">
        <label htmlFor={descriptionId} className="block text-sm font-medium text-slate-700">
          Description <span className="font-normal text-slate-500">(optional)</span>
        </label>
        <textarea
          id={descriptionId}
          rows={2}
          maxLength={500}
          value={description}
          onChange={(e) => {
            onDescription(e.target.value)
          }}
          className={fieldClass}
        />
      </div>
    </div>
  )
}
