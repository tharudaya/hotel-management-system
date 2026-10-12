import { type SubmitEvent, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { ApiError } from '../../api/client'
import { useAuthenticatedUser } from '../../auth/currentUser'
import { FormAlert } from '../../components/AuthShell'
import { buttonPrimary, buttonSecondary, focusRing } from '../../components/styles'
import { messageFor } from '../users/form'
import { PermissionEditor } from './PermissionEditor'
import { useCreateRole, usePermissionCatalog } from './queries'
import { RoleFields } from './RoleFields'

/** /admin/roles/new */
export function NewRolePage() {
  const me = useAuthenticatedUser()
  const catalog = usePermissionCatalog()
  const create = useCreateRole()
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [nameError, setNameError] = useState<string | undefined>()
  const [formError, setFormError] = useState<string | null>(null)
  const nameRef = useRef<HTMLInputElement>(null)

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    setFormError(null)
    if (!name.trim()) {
      setNameError('Enter a name for the role.')
      nameRef.current?.focus()
      return
    }
    create.mutate(
      {
        name: name.trim(),
        description: description.trim() || null,
        permissions: [...selected].sort(),
      },
      {
        onSuccess: (role) => {
          void navigate(`/admin/roles/${role.id}`, {
            replace: true,
            state: { notice: `${role.name} was created.` },
          })
        },
        onError: (error) => {
          if (error instanceof ApiError && error.code === 'role_name_taken') {
            setNameError(error.message)
            nameRef.current?.focus()
          } else {
            setFormError(messageFor(error))
          }
        },
      },
    )
  }

  return (
    <section aria-labelledby="new-role-heading" className="space-y-4">
      <Link
        to="/admin/roles"
        className={`inline-block rounded-sm text-sm text-sky-800 hover:underline ${focusRing}`}
      >
        ← All roles
      </Link>
      <h1 id="new-role-heading" className="text-xl font-semibold text-slate-900">
        Create role
      </h1>
      <form
        onSubmit={handleSubmit}
        noValidate
        aria-labelledby="new-role-heading"
        className="space-y-6 rounded-lg bg-white p-6 shadow-sm"
      >
        <FormAlert message={formError} />
        <RoleFields
          name={name}
          description={description}
          nameError={nameError}
          nameRef={nameRef}
          onName={(value) => {
            setName(value)
            setNameError(undefined)
          }}
          onDescription={setDescription}
        />
        <div className="space-y-2">
          <h2 className="text-lg font-semibold text-slate-900">Permissions</h2>
          {catalog.isPending ? (
            <p role="status" className="text-sm text-slate-600">
              Loading permissions…
            </p>
          ) : catalog.isError ? (
            <p role="alert" className="text-sm text-slate-800">
              The permission list could not be loaded.
            </p>
          ) : (
            <PermissionEditor
              groups={catalog.data}
              selected={selected}
              onChange={setSelected}
              canGrant={(key) => me.permissions.includes(key)}
            />
          )}
        </div>
        <div className="flex gap-3">
          <button type="submit" disabled={create.isPending} className={buttonPrimary}>
            {create.isPending ? 'Creating…' : 'Create role'}
          </button>
          <Link to="/admin/roles" className={buttonSecondary}>
            Cancel
          </Link>
        </div>
      </form>
    </section>
  )
}
