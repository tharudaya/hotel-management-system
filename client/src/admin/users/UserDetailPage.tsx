import { type SubmitEvent, useEffect, useId, useRef, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router'

import { ApiError } from '../../api/client'
import type { LinkIssued, UserAction, UserDetail } from '../../api/types'
import { Can } from '../../auth/Can'
import { useAuthenticatedUser } from '../../auth/currentUser'
import { P } from '../../auth/permissions'
import { usePermissions } from '../../auth/usePermissions'
import { FormAlert } from '../../components/AuthShell'
import { Dialog } from '../../components/Dialog'
import { GuardedButton } from '../../components/GuardedButton'
import { buttonPrimary, buttonSecondary, fieldClass, focusRing } from '../../components/styles'
import { formatDateTime, plural } from '../../lib/format'
import { describeAuditEntry } from '../audit'
import { Field } from './Field'
import {
  endOfDay,
  type FieldErrors,
  fieldForError,
  messageFor,
  optional,
  toDateInput,
  validateEmail,
} from './form'
import { LinkIssuedResult } from './LinkIssuedResult'
import {
  type UserActionPath,
  useAssignableRoles,
  useAssignRole,
  useUpdateUser,
  useUser,
  useUserAction,
  useUserAudit,
} from './queries'
import { StatusBadge } from './StatusBadge'

const sectionClass = 'space-y-4 rounded-lg bg-white p-6 shadow-sm'
const buttonDanger =
  'inline-flex items-center justify-center gap-2 rounded-md bg-red-700 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-red-800 disabled:cursor-not-allowed disabled:opacity-60 ' +
  focusRing

/** /admin/users/:userId */
export function UserDetailPage() {
  const { userId } = useParams()
  const location = useLocation()
  const id = Number(userId)
  const valid = Number.isInteger(id) && id > 0
  const user = useUser(valid ? id : 0)
  const listSearch = listSearchFrom(location.state)

  const back = (
    <Link
      to={`/admin/users${listSearch}`}
      className={`inline-block rounded-sm text-sm text-sky-800 hover:underline ${focusRing}`}
    >
      ← All users
    </Link>
  )

  if (!valid || (user.error instanceof ApiError && user.error.status === 404)) {
    return (
      <section className="space-y-4">
        {back}
        <h1 className="text-xl font-semibold text-slate-900">User not found</h1>
        <p className="text-slate-700">There is no user with this ID.</p>
      </section>
    )
  }
  if (user.isPending) {
    return (
      <section className="space-y-4">
        {back}
        <p role="status" className="text-slate-600">
          Loading user…
        </p>
      </section>
    )
  }
  if (user.isError) {
    return (
      <section className="space-y-4">
        {back}
        <div role="alert" className="space-y-3">
          <p className="text-slate-800">This user could not be loaded.</p>
          <button
            type="button"
            onClick={() => {
              void user.refetch()
            }}
            className={buttonSecondary}
          >
            Try again
          </button>
        </div>
      </section>
    )
  }

  const detail = user.data
  return (
    <section aria-labelledby="user-heading" className="space-y-4">
      {back}
      <div className="flex flex-wrap items-center gap-3">
        <h1 id="user-heading" className="text-xl font-semibold text-slate-900">
          {detail.full_name}
        </h1>
        <span className="text-slate-600">{detail.username}</span>
        <StatusBadge status={detail.status} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <ProfileSection user={detail} />
        <div className="space-y-4">
          <RoleSection user={detail} />
          <ActivitySection user={detail} />
        </div>
      </div>
      <Can permission={P.USER_MANAGE}>
        <ActionsSection user={detail} />
      </Can>
      <Can permission={P.AUDIT_VIEW}>
        <AuditSection userId={detail.id} />
      </Can>
    </section>
  )
}

function listSearchFrom(state: unknown): string {
  if (typeof state === 'object' && state !== null && 'listSearch' in state) {
    const { listSearch } = state
    if (typeof listSearch === 'string' && listSearch.startsWith('?')) return listSearch
  }
  return ''
}

function blocked(user: UserDetail, action: UserAction): string | null {
  return user.restrictions[action] ?? null
}

// --- profile ------------------------------------------------------------------------------

type ProfileField = 'full_name' | 'email' | 'phone' | 'job_title' | 'employee_id' | 'expires'

function profileValues(user: UserDetail): Record<ProfileField, string> {
  return {
    full_name: user.full_name,
    email: user.email ?? '',
    phone: user.phone ?? '',
    job_title: user.job_title ?? '',
    employee_id: user.employee_id ?? '',
    expires: toDateInput(user.expires_at),
  }
}

function ProfileSection({ user }: { user: UserDetail }) {
  const { can } = usePermissions()
  const [editing, setEditing] = useState(false)
  const editButtonRef = useRef<HTMLButtonElement>(null)
  const wasEditing = useRef(false)

  useEffect(() => {
    // Back from the form: return focus to the Edit button.
    if (wasEditing.current && !editing) editButtonRef.current?.focus()
    wasEditing.current = editing
  }, [editing])

  return (
    <section aria-labelledby="profile-heading" className={sectionClass}>
      <div className="flex items-center justify-between gap-3">
        <h2 id="profile-heading" className="text-lg font-semibold text-slate-900">
          Profile
        </h2>
        {can(P.USER_MANAGE) && !editing && (
          <GuardedButton
            ref={editButtonRef}
            blockedReason={blocked(user, 'edit')}
            onClick={() => {
              setEditing(true)
            }}
            className={buttonSecondary}
          >
            Edit profile
          </GuardedButton>
        )}
      </div>
      {editing ? (
        <ProfileForm
          user={user}
          onDone={() => {
            setEditing(false)
          }}
        />
      ) : (
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
          <Row label="Name">{user.full_name}</Row>
          <Row label="Username">{user.username}</Row>
          <Row label="Email">{user.email ?? <None />}</Row>
          <Row label="Phone">{user.phone ?? <None />}</Row>
          <Row label="Job title">{user.job_title ?? <None />}</Row>
          <Row label="Employee ID">{user.employee_id ?? <None />}</Row>
        </dl>
      )}
    </section>
  )
}

function ProfileForm({ user, onDone }: { user: UserDetail; onDone: () => void }) {
  const update = useUpdateUser(user.id)
  const initial = profileValues(user)
  const [values, setValues] = useState(initial)
  const [errors, setErrors] = useState<FieldErrors<ProfileField>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  const emailRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    nameRef.current?.focus()
  }, [])

  function set(field: ProfileField, value: string) {
    setValues((current) => ({ ...current, [field]: value }))
    setErrors((current) => ({ ...current, [field]: undefined }))
  }

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    setFormError(null)
    const found: FieldErrors<ProfileField> = {}
    if (!values.full_name.trim()) found.full_name = 'Enter the full name.'
    const emailError = validateEmail(values.email)
    if (emailError) found.email = emailError
    if (found.full_name || found.email) {
      setErrors(found)
      ;(found.full_name ? nameRef : emailRef).current?.focus()
      return
    }

    // Send only what changed.
    const body: Record<string, string | null> = {}
    const changed = (field: ProfileField) => values[field] !== initial[field]
    if (changed('full_name')) body.full_name = values.full_name.trim()
    for (const field of ['email', 'phone', 'job_title', 'employee_id'] as const) {
      if (changed(field)) body[field] = optional(values[field])
    }
    if (changed('expires')) body.expires_at = endOfDay(values.expires)
    if (Object.keys(body).length === 0) {
      onDone()
      return
    }
    update.mutate(body, {
      onSuccess: onDone,
      onError: (error) => {
        if (fieldForError(error) === 'email') {
          setErrors({ email: error.message })
          emailRef.current?.focus()
        } else {
          setFormError(messageFor(error))
        }
      },
    })
  }

  return (
    <form onSubmit={handleSubmit} noValidate aria-label="Edit profile" className="space-y-4">
      <FormAlert message={formError} />
      <Field
        ref={nameRef}
        label="Full name"
        required
        value={values.full_name}
        error={errors.full_name}
        onChange={(e) => {
          set('full_name', e.target.value)
        }}
      />
      <Field
        ref={emailRef}
        label="Email"
        type="email"
        value={values.email}
        error={errors.email}
        onChange={(e) => {
          set('email', e.target.value)
        }}
      />
      <Field
        label="Phone"
        type="tel"
        value={values.phone}
        onChange={(e) => {
          set('phone', e.target.value)
        }}
      />
      <Field
        label="Job title"
        value={values.job_title}
        onChange={(e) => {
          set('job_title', e.target.value)
        }}
      />
      <Field
        label="Employee ID"
        value={values.employee_id}
        onChange={(e) => {
          set('employee_id', e.target.value)
        }}
      />
      <Field
        label="Access expires after"
        type="date"
        hint="Leave empty for no expiry."
        value={values.expires}
        onChange={(e) => {
          set('expires', e.target.value)
        }}
      />
      <div className="flex gap-3">
        <button type="submit" disabled={update.isPending} className={buttonPrimary}>
          {update.isPending ? 'Saving…' : 'Save'}
        </button>
        <button type="button" onClick={onDone} className={buttonSecondary}>
          Cancel
        </button>
      </div>
    </form>
  )
}

// --- role ----------------------------------------------------------------------------------

function RoleSection({ user }: { user: UserDetail }) {
  const { can } = usePermissions()
  const current = user.roles[0]?.role ?? null
  return (
    <section aria-labelledby="role-heading" className={sectionClass}>
      <h2 id="role-heading" className="text-lg font-semibold text-slate-900">
        Role
      </h2>
      {can(P.USER_MANAGE) ? (
        <RoleForm user={user} currentRoleId={current?.id ?? null} />
      ) : (
        <p className="text-slate-800">{current?.name ?? 'No role'}</p>
      )}
    </section>
  )
}

function RoleForm({ user, currentRoleId }: { user: UserDetail; currentRoleId: number | null }) {
  const roles = useAssignableRoles()
  const assign = useAssignRole(user.id)
  const [selected, setSelected] = useState(currentRoleId === null ? '' : String(currentRoleId))
  const [confirming, setConfirming] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const selectId = useId()
  const reason = blocked(user, 'change_role')

  // Only roles you may assign, plus the current one so the select shows it.
  const options = roles.data?.filter((role) => role.assignable || role.id === currentRoleId) ?? []
  const target = options.find((role) => String(role.id) === selected)
  const unchanged = selected === (currentRoleId === null ? '' : String(currentRoleId))

  return (
    <form
      aria-label="Change role"
      onSubmit={(e) => {
        e.preventDefault()
      }}
      className="space-y-3"
    >
      <div className="space-y-1">
        <label htmlFor={selectId} className="block text-sm font-medium text-slate-700">
          Role at this property
        </label>
        <select
          id={selectId}
          value={selected}
          disabled={reason !== null}
          onChange={(e) => {
            setSelected(e.target.value)
            setDone(null)
          }}
          className={fieldClass}
        >
          {currentRoleId === null && <option value="">No role</option>}
          {options.map((role) => (
            <option key={role.id} value={role.id} disabled={!role.assignable}>
              {role.name}
            </option>
          ))}
        </select>
      </div>
      <GuardedButton
        blockedReason={reason ?? (unchanged ? 'Choose a different role first.' : null)}
        onClick={() => {
          setConfirming(true)
        }}
        className={buttonSecondary}
      >
        Change role
      </GuardedButton>
      <p role="status" className="text-sm text-emerald-800">
        {done}
      </p>
      {confirming && target && (
        <ConfirmDialog
          title={`Change ${user.full_name}'s role?`}
          description={
            <>
              {user.full_name} ({user.username}) will get the role <strong>{target.name}</strong>{' '}
              and be signed out of all sessions.
            </>
          }
          confirmLabel="Change role"
          pending={assign.isPending}
          error={assign.isError ? messageFor(assign.error) : null}
          onConfirm={() => {
            assign.mutate(target.id, {
              onSuccess: () => {
                setConfirming(false)
                setDone(`${user.full_name} now has the role ${target.name}.`)
              },
            })
          }}
          onCancel={() => {
            assign.reset()
            setConfirming(false)
          }}
        />
      )}
    </form>
  )
}

// --- status and activity -------------------------------------------------------------------

function ActivitySection({ user }: { user: UserDetail }) {
  const { property } = useAuthenticatedUser()
  const when = (iso: string) => (
    <time dateTime={iso}>{formatDateTime(iso, property.time_zone)}</time>
  )
  return (
    <section aria-labelledby="activity-heading" className={sectionClass}>
      <h2 id="activity-heading" className="text-lg font-semibold text-slate-900">
        Status and activity
      </h2>
      {user.status === 'invited' && (
        <p className="rounded-md bg-sky-50 px-3 py-2 text-sm text-sky-900">
          Invited: has not activated their account yet.
        </p>
      )}
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
        <Row label="Status">
          <StatusBadge status={user.status} />
        </Row>
        <Row label="Created">{when(user.created_at)}</Row>
        <Row label="Last login">{user.last_login_at ? when(user.last_login_at) : 'Never'}</Row>
        <Row label="Access expires">{user.expires_at ? when(user.expires_at) : 'No expiry'}</Row>
        {user.deactivated_at && <Row label="Deactivated">{when(user.deactivated_at)}</Row>}
      </dl>
    </section>
  )
}

// --- actions -------------------------------------------------------------------------------

interface ActionConfig {
  path: UserActionPath
  restriction: UserAction
  label: string
  title: (user: UserDetail) => string
  description: (user: UserDetail) => string
  confirmLabel: string
  danger?: boolean
  done?: (user: UserDetail, result: unknown) => string
}

const ACTIONS: Record<UserActionPath, ActionConfig> = {
  invite: {
    path: 'invite',
    restriction: 'send_invite',
    label: 'Resend invite',
    title: (u) => `Send a new invite to ${u.full_name}?`,
    description: (u) =>
      `${u.email ? `It will be emailed to ${u.email}.` : 'You will get an activation link to give them in person.'} Any earlier invite link stops working.`,
    confirmLabel: 'Send invite',
  },
  'reset-password': {
    path: 'reset-password',
    restriction: 'reset_password',
    label: 'Reset password',
    title: (u) => `Reset the password for ${u.full_name}?`,
    description: (u) =>
      `${u.email ? `A reset link will be emailed to ${u.email}.` : 'You will get a reset link to give them in person.'} The link works for 1 hour; their current password keeps working until they use it.`,
    confirmLabel: 'Send reset link',
  },
  'revoke-sessions': {
    path: 'revoke-sessions',
    restriction: 'revoke_sessions',
    label: 'Sign out everywhere',
    title: (u) => `Sign ${u.full_name} out everywhere?`,
    description: (u) =>
      `All of ${u.full_name}'s (${u.username}) active sessions end. They can sign in again.`,
    confirmLabel: 'Sign out everywhere',
    danger: true,
    done: (u, result) => {
      const n =
        typeof result === 'object' && result !== null && 'revoked_sessions' in result
          ? Number(result.revoked_sessions)
          : 0
      return `${u.full_name} was signed out of ${plural(n, 'session')}.`
    },
  },
  deactivate: {
    path: 'deactivate',
    restriction: 'deactivate',
    label: 'Deactivate',
    title: (u) => `Deactivate ${u.full_name}?`,
    description: (u) =>
      `${u.full_name} (${u.username}) is signed out and can no longer sign in. You can reactivate them later.`,
    confirmLabel: 'Deactivate',
    danger: true,
    done: (u) => `${u.full_name} was deactivated.`,
  },
  reactivate: {
    path: 'reactivate',
    restriction: 'reactivate',
    label: 'Reactivate',
    title: (u) => `Reactivate ${u.full_name}?`,
    description: (u) => `${u.full_name} (${u.username}) can sign in again.`,
    confirmLabel: 'Reactivate',
    done: (u) => `${u.full_name} was reactivated.`,
  },
}

function ActionsSection({ user }: { user: UserDetail }) {
  const [open, setOpen] = useState<UserActionPath | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const linkAction: UserActionPath = user.status === 'invited' ? 'invite' : 'reset-password'
  const statusAction: UserActionPath = user.status === 'deactivated' ? 'reactivate' : 'deactivate'

  return (
    <section aria-labelledby="actions-heading" className={sectionClass}>
      <h2 id="actions-heading" className="text-lg font-semibold text-slate-900">
        Actions
      </h2>
      <ul className="flex flex-wrap gap-3">
        {/* Keys by slot, not action: the same button stays (and keeps focus) when
            Deactivate turns into Reactivate. */}
        {(
          [
            ['link', linkAction],
            ['sessions', 'revoke-sessions'],
            ['status', statusAction],
          ] as const
        ).map(([slot, action]) => {
          const config = ACTIONS[action]
          return (
            <li key={slot}>
              <GuardedButton
                blockedReason={blocked(user, config.restriction)}
                onClick={() => {
                  setMessage(null)
                  setOpen(action)
                }}
                className={buttonSecondary}
              >
                {config.label}
              </GuardedButton>
            </li>
          )
        })}
      </ul>
      <p role="status" className="text-sm text-emerald-800">
        {message}
      </p>
      {open && (
        <ActionDialog
          key={open}
          user={user}
          config={ACTIONS[open]}
          onDone={(text) => {
            setOpen(null)
            if (text) setMessage(text)
          }}
        />
      )}
    </section>
  )
}

function ActionDialog({
  user,
  config,
  onDone,
}: {
  user: UserDetail
  config: ActionConfig
  onDone: (message: string | null) => void
}) {
  const action = useUserAction(user.id, config.path)
  const [link, setLink] = useState<LinkIssued | null>(null)

  if (link) {
    return (
      <Dialog
        title={config.path === 'invite' ? 'Invite ready' : 'Reset link ready'}
        onClose={() => {
          onDone(null)
        }}
      >
        <div className="space-y-4">
          <LinkIssuedResult
            result={link}
            what={config.path === 'invite' ? 'invite' : 'password reset link'}
          />
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => {
                onDone(null)
              }}
              className={buttonPrimary}
            >
              Done
            </button>
          </div>
        </div>
      </Dialog>
    )
  }

  return (
    <ConfirmDialog
      title={config.title(user)}
      description={config.description(user)}
      confirmLabel={config.confirmLabel}
      danger={config.danger}
      pending={action.isPending}
      error={action.isError ? messageFor(action.error) : null}
      onConfirm={() => {
        action.mutate(undefined, {
          onSuccess: (result) => {
            if ('delivery' in result) setLink(result)
            else onDone(config.done?.(user, result) ?? null)
          },
        })
      }}
      onCancel={() => {
        onDone(null)
      }}
    />
  )
}

function ConfirmDialog({
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
  description: React.ReactNode
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

// --- audit ---------------------------------------------------------------------------------

function AuditSection({ userId }: { userId: number }) {
  const audit = useUserAudit(userId, true)
  const { property } = useAuthenticatedUser()
  return (
    <section aria-labelledby="audit-heading" className={sectionClass}>
      <h2 id="audit-heading" className="text-lg font-semibold text-slate-900">
        Recent changes
      </h2>
      {audit.isPending ? (
        <p role="status" className="text-sm text-slate-600">
          Loading history…
        </p>
      ) : audit.isError ? (
        <p role="alert" className="text-sm text-slate-800">
          The history could not be loaded.
        </p>
      ) : audit.data.items.length === 0 ? (
        <p className="text-sm text-slate-600">No changes recorded yet.</p>
      ) : (
        <ol className="divide-y divide-slate-100 text-sm">
          {audit.data.items.map((entry) => (
            <li key={entry.id} className="flex flex-wrap gap-x-4 gap-y-1 py-2">
              <time dateTime={entry.created_at} className="w-40 shrink-0 text-slate-600">
                {formatDateTime(entry.created_at, property.time_zone)}
              </time>
              <span className="text-slate-900">{describeAuditEntry(entry)}</span>
              <span className="text-slate-600">
                by {entry.actor ? entry.actor.full_name : 'the system'}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

// --- bits ----------------------------------------------------------------------------------

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-slate-600">{label}</dt>
      <dd className="text-slate-900">{children}</dd>
    </>
  )
}

function None() {
  return <span className="text-slate-400">Not set</span>
}
