import { Plus } from 'lucide-react'
import { type SubmitEvent, useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'

import { ApiError } from '../../api/client'
import type { RoleSummary } from '../../api/types'
import { Can } from '../../auth/Can'
import { P } from '../../auth/permissions'
import { usePermissions } from '../../auth/usePermissions'
import { FormAlert } from '../../components/AuthShell'
import { Dialog } from '../../components/Dialog'
import { GuardedButton } from '../../components/GuardedButton'
import { buttonPrimary, buttonSecondary, focusRing } from '../../components/styles'
import { Field } from '../users/Field'
import { messageFor } from '../users/form'
import { noticeFrom } from './notice'
import { useCreateRole, useRoles } from './queries'
import { RoleTypeBadge } from './RoleTypeBadge'

/** /admin/roles */
export function RolesListPage() {
  const roles = useRoles()
  const { can } = usePermissions()
  const location = useLocation()
  const notice = noticeFrom(location.state)
  const noticeRef = useRef<HTMLParagraphElement>(null)
  const [duplicating, setDuplicating] = useState<RoleSummary | null>(null)

  useEffect(() => {
    noticeRef.current?.focus()
  }, [notice])

  return (
    <section aria-labelledby="roles-heading" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 id="roles-heading" className="text-xl font-semibold text-slate-900">
          Roles
        </h1>
        <Can permission={P.ROLE_MANAGE}>
          <Link to="/admin/roles/new" className={buttonPrimary}>
            <Plus aria-hidden="true" className="size-4" />
            Create role
          </Link>
        </Can>
      </div>
      {notice && (
        <p
          ref={noticeRef}
          tabIndex={-1}
          role="status"
          className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-900 focus:outline-none"
        >
          {notice}
        </p>
      )}

      <div className="rounded-lg bg-white shadow-sm">
        {roles.isPending ? (
          <p role="status" className="p-6 text-slate-600">
            Loading roles…
          </p>
        ) : roles.isError ? (
          <div role="alert" className="space-y-3 p-6">
            <p className="text-slate-800">The roles could not be loaded.</p>
            <button
              type="button"
              onClick={() => {
                void roles.refetch()
              }}
              className={buttonSecondary}
            >
              Try again
            </button>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Roles</caption>
              <thead className="bg-slate-50 text-slate-700">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Role
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Type
                  </th>
                  <th scope="col" className="px-4 py-2 text-right font-semibold">
                    Users
                  </th>
                  <th scope="col" className="px-4 py-2 text-right font-semibold">
                    Permissions
                  </th>
                  {can(P.ROLE_MANAGE) && (
                    <th scope="col" className="px-4 py-2 font-semibold">
                      <span className="sr-only">Actions</span>
                    </th>
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {roles.data.map((role) => (
                  <tr key={role.id} className="hover:bg-slate-50">
                    <th scope="row" className="px-4 py-2 font-medium">
                      <Link
                        to={`/admin/roles/${role.id}`}
                        className={`rounded-sm text-sky-800 hover:underline ${focusRing}`}
                      >
                        {role.name}
                      </Link>
                      {role.description && (
                        <span className="block text-xs font-normal text-slate-500">
                          {role.description}
                        </span>
                      )}
                    </th>
                    <td className="px-4 py-2">
                      <RoleTypeBadge isSystem={role.is_system} isAdmin={role.is_admin} />
                    </td>
                    <td className="px-4 py-2 text-right text-slate-700 tabular-nums">
                      {role.user_count}
                    </td>
                    <td className="px-4 py-2 text-right text-slate-700 tabular-nums">
                      {role.permission_count}
                    </td>
                    {can(P.ROLE_MANAGE) && (
                      <td className="px-4 py-2 text-right">
                        <GuardedButton
                          aria-label={`Duplicate ${role.name}`}
                          blockedReason={
                            role.can_duplicate
                              ? null
                              : `${role.name} has permissions you don't have, so you can't copy it.`
                          }
                          onClick={() => {
                            setDuplicating(role)
                          }}
                          className={buttonSecondary}
                        >
                          Duplicate
                        </GuardedButton>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {duplicating && (
        <DuplicateDialog
          role={duplicating}
          onClose={() => {
            setDuplicating(null)
          }}
        />
      )}
    </section>
  )
}

function DuplicateDialog({ role, onClose }: { role: RoleSummary; onClose: () => void }) {
  const create = useCreateRole()
  const navigate = useNavigate()
  const [name, setName] = useState(`Copy of ${role.name}`)
  const [error, setError] = useState<string | undefined>()
  const [formError, setFormError] = useState<string | null>(null)
  const nameRef = useRef<HTMLInputElement>(null)

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    setFormError(null)
    if (!name.trim()) {
      setError('Enter a name for the new role.')
      nameRef.current?.focus()
      return
    }
    create.mutate(
      { name: name.trim(), copy_from_role_id: role.id },
      {
        onSuccess: (created) => {
          void navigate(`/admin/roles/${created.id}`, {
            state: { notice: `${created.name} was created as a copy of ${role.name}.` },
          })
        },
        onError: (e) => {
          if (e instanceof ApiError && e.code === 'role_name_taken') {
            setError(e.message)
            nameRef.current?.focus()
          } else {
            setFormError(messageFor(e))
          }
        },
      },
    )
  }

  return (
    <Dialog
      title={`Duplicate ${role.name}`}
      description={`The new role starts with the same ${role.permission_count} permissions and no users.`}
      onClose={onClose}
    >
      <form onSubmit={handleSubmit} noValidate className="space-y-4">
        <FormAlert message={formError} />
        <Field
          ref={nameRef}
          label="Name of the new role"
          required
          maxLength={100}
          value={name}
          error={error}
          onChange={(e) => {
            setName(e.target.value)
            setError(undefined)
          }}
        />
        <div className="flex justify-end gap-3">
          <button type="button" onClick={onClose} className={buttonSecondary}>
            Cancel
          </button>
          <button type="submit" disabled={create.isPending} className={buttonPrimary}>
            {create.isPending ? 'Creating…' : 'Create copy'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}
