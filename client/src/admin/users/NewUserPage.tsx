import { type RefObject, type SubmitEvent, useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router'

import type { UserDetail } from '../../api/types'
import { FormAlert } from '../../components/AuthShell'
import { buttonPrimary, buttonSecondary, fieldClass, focusRing } from '../../components/styles'
import { Field } from './Field'
import {
  endOfDay,
  type FieldErrors,
  fieldForError,
  messageFor,
  optional,
  USERNAME_PATTERN,
  validateEmail,
} from './form'
import { LinkIssuedResult } from './LinkIssuedResult'
import { useAssignableRoles, useCreateUser, useUserAction } from './queries'

type NewUserField = 'full_name' | 'username' | 'email' | 'role'

const EMPTY = {
  full_name: '',
  username: '',
  email: '',
  phone: '',
  job_title: '',
  employee_id: '',
  role: '',
  expires: '',
}

/** /admin/users/new: create an invited user, then offer the invite. */
export function NewUserPage() {
  const [created, setCreated] = useState<UserDetail | null>(null)
  return (
    <section aria-labelledby="new-user-heading" className="space-y-4">
      <Link
        to="/admin/users"
        className={`inline-block rounded-sm text-sm text-sky-800 hover:underline ${focusRing}`}
      >
        ← All users
      </Link>
      <h1 id="new-user-heading" className="text-xl font-semibold text-slate-900">
        Add user
      </h1>
      {created ? (
        <Created
          user={created}
          onAnother={() => {
            setCreated(null)
          }}
        />
      ) : (
        <NewUserForm onCreated={setCreated} />
      )}
    </section>
  )
}

function NewUserForm({ onCreated }: { onCreated: (user: UserDetail) => void }) {
  const create = useCreateUser()
  const roles = useAssignableRoles()
  const [values, setValues] = useState(EMPTY)
  const [errors, setErrors] = useState<FieldErrors<NewUserField>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  const usernameRef = useRef<HTMLInputElement>(null)
  const emailRef = useRef<HTMLInputElement>(null)
  const roleRef = useRef<HTMLSelectElement>(null)
  const roleId = useId()
  const roleErrorId = useId()
  const assignable = roles.data?.filter((role) => role.assignable) ?? []

  function set(field: keyof typeof EMPTY, value: string) {
    setValues((current) => ({ ...current, [field]: value }))
    setErrors((current) => ({ ...current, [field]: undefined }))
  }

  function showErrors(found: FieldErrors<NewUserField>) {
    setErrors(found)
    // Focus the first field with an error, in form order.
    const fields: [string | undefined, RefObject<HTMLElement | null>][] = [
      [found.full_name, nameRef],
      [found.username, usernameRef],
      [found.email, emailRef],
      [found.role, roleRef],
    ]
    fields.find(([error]) => error)?.[1].current?.focus()
  }

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    setFormError(null)
    const found: FieldErrors<NewUserField> = {}
    if (!values.full_name.trim()) found.full_name = 'Enter the full name.'
    if (!USERNAME_PATTERN.test(values.username.trim())) {
      found.username = 'Use 3–50 letters, digits, dots, hyphens or underscores.'
    }
    const emailError = validateEmail(values.email)
    if (emailError) found.email = emailError
    if (!values.role) found.role = 'Choose a role.'
    if (Object.keys(found).length > 0) {
      showErrors(found)
      return
    }
    create.mutate(
      {
        full_name: values.full_name.trim(),
        username: values.username.trim(),
        email: optional(values.email),
        phone: optional(values.phone),
        job_title: optional(values.job_title),
        employee_id: optional(values.employee_id),
        expires_at: endOfDay(values.expires),
        role_id: Number(values.role),
      },
      {
        onSuccess: onCreated,
        onError: (error) => {
          const field = fieldForError(error)
          if (field) showErrors({ [field]: error.message })
          else setFormError(messageFor(error))
        },
      },
    )
  }

  return (
    <form
      onSubmit={handleSubmit}
      noValidate
      aria-labelledby="new-user-heading"
      className="max-w-2xl space-y-5 rounded-lg bg-white p-6 shadow-sm"
    >
      <FormAlert message={formError} />
      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          ref={nameRef}
          label="Full name"
          required
          autoComplete="off"
          value={values.full_name}
          error={errors.full_name}
          onChange={(e) => {
            set('full_name', e.target.value)
          }}
        />
        <Field
          ref={usernameRef}
          label="Username"
          required
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          hint="3–50 letters, digits, dots, hyphens or underscores. Used to sign in."
          value={values.username}
          error={errors.username}
          onChange={(e) => {
            set('username', e.target.value)
          }}
        />
        <Field
          ref={emailRef}
          label="Email"
          type="email"
          autoComplete="off"
          hint="Without an email, you can share an activation link in person."
          value={values.email}
          error={errors.email}
          onChange={(e) => {
            set('email', e.target.value)
          }}
        />
        <Field
          label="Phone"
          type="tel"
          autoComplete="off"
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
        <div className="space-y-1">
          <label htmlFor={roleId} className="block text-sm font-medium text-slate-700">
            Role
          </label>
          <select
            ref={roleRef}
            id={roleId}
            required
            value={values.role}
            aria-invalid={errors.role ? true : undefined}
            aria-describedby={errors.role ? roleErrorId : undefined}
            onChange={(e) => {
              set('role', e.target.value)
            }}
            className={`${fieldClass} aria-invalid:border-red-600`}
          >
            <option value="">{roles.isPending ? 'Loading roles…' : 'Choose a role'}</option>
            {assignable.map((role) => (
              <option key={role.id} value={role.id}>
                {role.name}
              </option>
            ))}
          </select>
          {errors.role && (
            <p id={roleErrorId} className="text-sm text-red-700">
              {errors.role}
            </p>
          )}
        </div>
        <Field
          label="Access expires after"
          type="date"
          hint="For temporary staff: they cannot sign in after this day."
          value={values.expires}
          onChange={(e) => {
            set('expires', e.target.value)
          }}
        />
      </div>
      <div className="flex gap-3">
        <button type="submit" disabled={create.isPending} className={buttonPrimary}>
          {create.isPending ? 'Creating…' : 'Create user'}
        </button>
        <Link to="/admin/users" className={buttonSecondary}>
          Cancel
        </Link>
      </div>
    </form>
  )
}

/** After creation: the user is invited; send the invite or show the link. */
function Created({ user, onAnother }: { user: UserDetail; onAnother: () => void }) {
  const invite = useUserAction(user.id, 'invite')
  const headingRef = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    headingRef.current?.focus()
  }, [])

  return (
    <div className="max-w-2xl space-y-4 rounded-lg bg-white p-6 shadow-sm">
      <h2
        ref={headingRef}
        tabIndex={-1}
        className="text-lg font-semibold text-slate-900 focus:outline-none"
      >
        {user.full_name} ({user.username}) was created
      </h2>
      <p className="text-slate-700">
        They are <strong>invited</strong>: they can sign in once they have chosen a password with an
        activation link.
      </p>
      {invite.isSuccess ? (
        <LinkIssuedResult result={invite.data} what="invite" />
      ) : (
        <div className="space-y-2">
          <button
            type="button"
            disabled={invite.isPending}
            onClick={() => {
              invite.mutate()
            }}
            className={buttonPrimary}
          >
            {user.email ? 'Send invite' : 'Show activation link'}
          </button>
          <FormAlert message={invite.isError ? messageFor(invite.error) : null} />
        </div>
      )}
      <div className="flex flex-wrap gap-3 border-t border-slate-200 pt-4">
        <Link to={`/admin/users/${user.id}`} className={buttonSecondary}>
          View user
        </Link>
        <button type="button" onClick={onAnother} className={buttonSecondary}>
          Add another user
        </button>
      </div>
    </div>
  )
}
