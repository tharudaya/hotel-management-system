import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router'

import { ApiError } from '../../api/client'
import type { PermissionGroup, RoleDetail, RoleUpdate } from '../../api/types'
import { useAuthenticatedUser } from '../../auth/currentUser'
import { P } from '../../auth/permissions'
import { usePermissions } from '../../auth/usePermissions'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { GuardedButton } from '../../components/GuardedButton'
import { buttonPrimary, buttonSecondary, focusRing } from '../../components/styles'
import { plural } from '../../lib/format'
import { StatusBadge } from '../users/StatusBadge'
import { messageFor } from '../users/form'
import { describePermissions } from './catalog'
import { noticeFrom } from './notice'
import { PermissionEditor } from './PermissionEditor'
import { RoleFields } from './RoleFields'
import { useDeleteRole, usePermissionCatalog, useRole, useUpdateRole } from './queries'
import { RoleTypeBadge } from './RoleTypeBadge'
import { useDocumentTitle } from '../../lib/useDocumentTitle'

const sectionClass = 'space-y-4 rounded-lg bg-white p-6 shadow-sm'

/** /admin/roles/:roleId */
export function RoleDetailPage() {
  const { roleId } = useParams()
  const id = Number(roleId)
  const valid = Number.isInteger(id) && id > 0
  const role = useRole(valid ? id : 0)
  useDocumentTitle(role.data?.name ?? 'Role')
  const catalog = usePermissionCatalog()

  const back = (
    <Link
      to="/admin/roles"
      className={`inline-block rounded-sm text-sm text-sky-800 hover:underline ${focusRing}`}
    >
      ← All roles
    </Link>
  )

  if (!valid || (role.error instanceof ApiError && role.error.status === 404)) {
    return (
      <section className="space-y-4">
        {back}
        <h1 className="text-xl font-semibold text-slate-900">Role not found</h1>
        <p className="text-slate-700">There is no role with this ID.</p>
      </section>
    )
  }
  if (role.isPending || catalog.isPending) {
    return (
      <section className="space-y-4">
        {back}
        <p role="status" className="text-slate-600">
          Loading role…
        </p>
      </section>
    )
  }
  if (role.isError || catalog.isError) {
    return (
      <section className="space-y-4">
        {back}
        <div role="alert" className="space-y-3">
          <p className="text-slate-800">This role could not be loaded.</p>
          <button
            type="button"
            onClick={() => {
              void role.refetch()
              void catalog.refetch()
            }}
            className={buttonSecondary}
          >
            Try again
          </button>
        </div>
      </section>
    )
  }

  return (
    <section aria-labelledby="role-heading" className="space-y-4">
      {back}
      <div className="flex flex-wrap items-center gap-3">
        <h1 id="role-heading" className="text-xl font-semibold text-slate-900">
          {role.data.name}
        </h1>
        <RoleTypeBadge isSystem={role.data.is_system} isAdmin={role.data.is_admin} />
      </div>
      <Notice />
      <RoleEditor key={role.data.id} role={role.data} groups={catalog.data} />
      <UsersSection role={role.data} />
      {!role.data.is_system && <DeleteSection role={role.data} />}
    </section>
  )
}

function Notice() {
  const location = useLocation()
  const notice = noticeFrom(location.state)
  const ref = useRef<HTMLParagraphElement>(null)
  useEffect(() => {
    ref.current?.focus()
  }, [notice])
  if (!notice) return null
  return (
    <p
      ref={ref}
      tabIndex={-1}
      role="status"
      className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-900 focus:outline-none"
    >
      {notice}
    </p>
  )
}

/** Why the role can't be edited by the signed-in user, or null if it can. */
function readOnlyReason(role: RoleDetail, canManage: boolean): string | null {
  if (role.is_admin) return 'The Admin role always has every permission and cannot be changed.'
  if (!canManage) return 'You can view this role but not change it.'
  if (!role.can_edit) {
    return "This role has permissions you don't have, so you can't change it."
  }
  return null
}

function RoleEditor({ role, groups }: { role: RoleDetail; groups: PermissionGroup[] }) {
  const me = useAuthenticatedUser()
  const { can } = usePermissions()
  const update = useUpdateRole(role.id)
  const [name, setName] = useState(role.name)
  const [description, setDescription] = useState(role.description ?? '')
  const [selected, setSelected] = useState<Set<string>>(() => new Set(role.permissions))
  const [nameError, setNameError] = useState<string | undefined>()
  const [confirming, setConfirming] = useState(false)
  const [saved, setSaved] = useState<string | null>(null)
  const nameRef = useRef<HTMLInputElement>(null)

  const lockedReason = readOnlyReason(role, can(P.ROLE_MANAGE))
  const editable = lockedReason === null
  // System roles keep their name and description; only permissions can change.
  const detailsEditable = editable && !role.is_system

  const original = new Set(role.permissions)
  const added = [...selected].filter((k) => !original.has(k)).sort()
  const removed = role.permissions.filter((k) => !selected.has(k))
  const nameChanged = name.trim() !== role.name
  const descriptionChanged = (description.trim() || null) !== role.description
  const dirty = added.length > 0 || removed.length > 0 || nameChanged || descriptionChanged

  function reviewChanges() {
    setSaved(null)
    if (!name.trim()) {
      setNameError('Enter a name for the role.')
      nameRef.current?.focus()
      return
    }
    setConfirming(true)
  }

  function save() {
    const body: RoleUpdate = {}
    if (nameChanged) body.name = name.trim()
    if (descriptionChanged) body.description = description.trim() || null
    if (added.length > 0 || removed.length > 0) body.permissions = [...selected].sort()
    update.mutate(body, {
      onSuccess: (updated) => {
        // Start again from what was saved (the same elements stay, so focus returns
        // to the Save button when the dialog closes).
        setName(updated.name)
        setDescription(updated.description ?? '')
        setSelected(new Set(updated.permissions))
        setConfirming(false)
        setSaved('Changes saved.')
      },
      onError: (error) => {
        if (error instanceof ApiError && error.code === 'role_name_taken') {
          setConfirming(false)
          setNameError(error.message)
          nameRef.current?.focus()
        }
      },
    })
  }

  return (
    <>
      <section aria-labelledby="details-heading" className={sectionClass}>
        <h2 id="details-heading" className="text-lg font-semibold text-slate-900">
          Details
        </h2>
        {detailsEditable ? (
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
        ) : (
          <>
            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
              <dt className="text-slate-600">Name</dt>
              <dd className="text-slate-900">{role.name}</dd>
              <dt className="text-slate-600">Description</dt>
              <dd className="text-slate-900">
                {role.description ?? <span className="text-slate-400">None</span>}
              </dd>
            </dl>
            {role.is_system && !role.is_admin && (
              <p className="text-sm text-slate-600">
                This is a system role: its name and description cannot be changed.
              </p>
            )}
          </>
        )}
      </section>

      <section aria-labelledby="permissions-heading" className={sectionClass}>
        <div>
          <h2 id="permissions-heading" className="text-lg font-semibold text-slate-900">
            Permissions
          </h2>
          <p className="text-sm text-slate-600">{plural(selected.size, 'permission')} selected</p>
        </div>
        {lockedReason && (
          <p className="rounded-md bg-slate-100 px-3 py-2 text-sm text-slate-800">{lockedReason}</p>
        )}
        <PermissionEditor
          groups={groups}
          selected={selected}
          onChange={(next) => {
            setSelected(next)
            setSaved(null)
          }}
          canGrant={(key) => me.permissions.includes(key)}
          readOnly={!editable}
        />
      </section>

      {editable && (
        <div className="sticky bottom-0 z-10 flex flex-wrap items-center gap-3 rounded-lg bg-white p-4 shadow-md ring-1 ring-slate-200">
          <GuardedButton
            blockedReason={dirty ? null : 'There are no changes to save.'}
            onClick={reviewChanges}
            className={buttonPrimary}
          >
            Save changes
          </GuardedButton>
          <button
            type="button"
            disabled={!dirty}
            onClick={() => {
              setName(role.name)
              setDescription(role.description ?? '')
              setSelected(new Set(role.permissions))
              setNameError(undefined)
            }}
            className={buttonSecondary}
          >
            Discard changes
          </button>
          <p role="status" className="text-sm text-slate-700">
            {saved ?? (dirty ? 'You have unsaved changes.' : '')}
          </p>
        </div>
      )}

      {confirming && (
        <ConfirmDialog
          title={`Save changes to ${role.name}?`}
          description={
            <SaveSummary
              groups={groups}
              added={added}
              removed={removed}
              userCount={role.user_count}
              nameChange={nameChanged ? [role.name, name.trim()] : null}
              descriptionChanged={descriptionChanged}
            />
          }
          confirmLabel="Save changes"
          pending={update.isPending}
          error={update.isError ? messageFor(update.error) : null}
          onConfirm={save}
          onCancel={() => {
            update.reset()
            setConfirming(false)
          }}
        />
      )}
    </>
  )
}

function SaveSummary({
  groups,
  added,
  removed,
  userCount,
  nameChange,
  descriptionChanged,
}: {
  groups: PermissionGroup[]
  added: string[]
  removed: string[]
  userCount: number
  nameChange: [string, string] | null
  descriptionChanged: boolean
}) {
  const describe = describePermissions(groups)
  const permissionsChanged = added.length > 0 || removed.length > 0
  return (
    <div className="space-y-3">
      {nameChange && (
        <p>
          Rename from <strong>{nameChange[0]}</strong> to <strong>{nameChange[1]}</strong>.
        </p>
      )}
      {descriptionChanged && <p>Update the description.</p>}
      {added.length > 0 && (
        <div>
          <p className="font-medium text-emerald-800">Added ({added.length})</p>
          <ul className="ml-5 list-disc">
            {added.map((key) => (
              <li key={key}>{describe.get(key) ?? key}</li>
            ))}
          </ul>
        </div>
      )}
      {removed.length > 0 && (
        <div>
          <p className="font-medium text-red-800">Removed ({removed.length})</p>
          <ul className="ml-5 list-disc">
            {removed.map((key) => (
              <li key={key}>{describe.get(key) ?? key}</li>
            ))}
          </ul>
        </div>
      )}
      {permissionsChanged && (
        <p>
          {userCount === 0
            ? 'No users have this role yet.'
            : `${plural(userCount, 'user')} ${userCount === 1 ? 'has' : 'have'} this role; their access changes right away.`}
        </p>
      )}
    </div>
  )
}

function UsersSection({ role }: { role: RoleDetail }) {
  const { can } = usePermissions()
  return (
    <section aria-labelledby="role-users-heading" className={sectionClass}>
      <h2 id="role-users-heading" className="text-lg font-semibold text-slate-900">
        Users with this role ({role.users.length})
      </h2>
      {role.users.length === 0 ? (
        <p className="text-sm text-slate-600">Nobody has this role.</p>
      ) : (
        <ul className="divide-y divide-slate-100 text-sm">
          {role.users.map((user) => (
            <li key={user.id} className="flex flex-wrap items-center gap-3 py-2">
              {can(P.USER_VIEW) ? (
                <Link
                  to={`/admin/users/${user.id}`}
                  className={`rounded-sm font-medium text-sky-800 hover:underline ${focusRing}`}
                >
                  {user.full_name}
                </Link>
              ) : (
                <span className="font-medium text-slate-900">{user.full_name}</span>
              )}
              <span className="text-slate-600">{user.username}</span>
              <StatusBadge status={user.status} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function DeleteSection({ role }: { role: RoleDetail }) {
  const { can } = usePermissions()
  const remove = useDeleteRole(role.id)
  const navigate = useNavigate()
  const [confirming, setConfirming] = useState(false)
  if (!can(P.ROLE_MANAGE)) return null

  const reason = !role.can_edit
    ? "This role has permissions you don't have, so you can't delete it."
    : role.user_count > 0
      ? `${plural(role.user_count, 'user')} still ${role.user_count === 1 ? 'has' : 'have'} this role. Give them another role first.`
      : null

  return (
    <section aria-labelledby="delete-heading" className={sectionClass}>
      <h2 id="delete-heading" className="text-lg font-semibold text-slate-900">
        Delete role
      </h2>
      <p className="text-sm text-slate-600">Only roles that nobody has can be deleted.</p>
      <GuardedButton
        blockedReason={reason}
        onClick={() => {
          setConfirming(true)
        }}
        className={buttonSecondary}
      >
        Delete {role.name}
      </GuardedButton>
      {confirming && (
        <ConfirmDialog
          title={`Delete ${role.name}?`}
          description="The role and its permission settings are removed. This cannot be undone."
          confirmLabel="Delete role"
          danger
          pending={remove.isPending}
          error={remove.isError ? messageFor(remove.error) : null}
          onConfirm={() => {
            remove.mutate(undefined, {
              onSuccess: () => {
                void navigate('/admin/roles', {
                  replace: true,
                  state: { notice: `${role.name} was deleted.` },
                })
              },
            })
          }}
          onCancel={() => {
            remove.reset()
            setConfirming(false)
          }}
        />
      )}
    </section>
  )
}
