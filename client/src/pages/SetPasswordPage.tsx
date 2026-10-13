import { type ReactNode, type SubmitEvent, useEffect, useId, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'

import { ApiError } from '../api/client'
import type { TokenInfo } from '../api/types'
import { type LinkKind, useLinkInfo, useSetPassword } from '../account/queries'
import {
  AuthShell,
  FormAlert,
  authButtonClass,
  authCardClass,
  authInputClass,
  authLinkClass,
} from '../components/AuthShell'

/** Mirrors the server's minimum (app/core/passwords.py); the server has the final say. */
export const MIN_PASSWORD_LENGTH = 12

const COPY = {
  activate: {
    title: 'Activate your account',
    greeting: (name: string) => `Welcome, ${name}`,
    intro: 'Choose a password to finish setting up your account.',
    submit: 'Activate account',
    pending: 'Activating…',
    doneTitle: 'Your account is ready',
    doneText: 'You can now sign in with your username and new password.',
    newLink: <>Ask your manager to send you a new invite.</>,
  },
  'reset-password': {
    title: 'Choose a new password',
    greeting: (name: string) => `Hello, ${name}`,
    intro: 'Choose a new password for your account.',
    submit: 'Change password',
    pending: 'Saving…',
    doneTitle: 'Password changed',
    doneText:
      'You can now sign in with your new password. Any other signed-in sessions have been signed out.',
    newLink: (
      <Link to="/forgot-password" className={authLinkClass}>
        Request a new reset link
      </Link>
    ),
  },
} satisfies Record<LinkKind, unknown>

const backToSignIn = (
  <Link to="/login" className={authLinkClass}>
    Back to sign in
  </Link>
)

/**
 * /activate#<token> and /reset-password#<token>. The token is in the fragment, which
 * browsers never send to a server; it is read once and then removed from the address
 * bar (and so from history).
 */
export function SetPasswordPage({ kind }: { kind: LinkKind }) {
  const location = useLocation()
  const navigate = useNavigate()
  const [token] = useState(() => location.hash.replace(/^#/, ''))
  useEffect(() => {
    if (location.hash) void navigate({ pathname: location.pathname, hash: '' }, { replace: true })
  }, [location.hash, location.pathname, navigate])
  const info = useLinkInfo(kind, token)
  const setPassword = useSetPassword(kind, token)
  const copy = COPY[kind]

  if (setPassword.isSuccess) {
    return (
      <AuthShell title={copy.doneTitle}>
        <Done text={copy.doneText} />
      </AuthShell>
    )
  }

  // The link can also turn out bad on submit (e.g. used in another tab meanwhile).
  // No token in the fragment (or a link copied without it): nothing to check.
  const linkError =
    token === ''
      ? 'This link is not valid.'
      : (linkProblem(info.error, { malformedIs422: true }) ?? linkProblem(setPassword.error))
  if (linkError) {
    return (
      <AuthShell title={copy.title} footer={backToSignIn}>
        <div className={`space-y-3 ${authCardClass}`}>
          <div role="alert" className="space-y-1">
            <p className="font-medium text-slate-900">{linkError}</p>
          </div>
          <p className="text-sm text-slate-700">{copy.newLink}</p>
        </div>
      </AuthShell>
    )
  }

  if (info.isPending) {
    return (
      <AuthShell title={copy.title}>
        <p role="status" className={`text-center text-slate-600 ${authCardClass}`}>
          Checking your link…
        </p>
      </AuthShell>
    )
  }

  if (info.isError) {
    return (
      <AuthShell title={copy.title} footer={backToSignIn}>
        <div className={`space-y-4 ${authCardClass}`}>
          <FormAlert message="We could not check your link. Please try again." />
          <button
            type="button"
            className={authButtonClass}
            onClick={() => {
              void info.refetch()
            }}
          >
            Try again
          </button>
        </div>
      </AuthShell>
    )
  }

  return (
    <AuthShell title={copy.title} footer={backToSignIn}>
      <PasswordForm
        account={info.data}
        greeting={copy.greeting(info.data.full_name)}
        intro={copy.intro}
        submitLabel={setPassword.isPending ? copy.pending : copy.submit}
        pending={setPassword.isPending}
        onSubmit={(password, handlers) => {
          setPassword.mutate(password, { onError: handlers.onError })
        }}
      />
    </AuthShell>
  )
}

/** Our own wording for a bad link; null for anything that is not a link problem. */
function linkProblem(error: Error | null, { malformedIs422 = false } = {}): string | null {
  if (!(error instanceof ApiError)) return null
  if (error.status === 410 && error.code === 'token_used') return 'This link has already been used.'
  if (error.status === 410) return 'This link has expired.'
  if (error.status === 404) return 'This link is not valid.'
  if (error.status === 429) {
    return 'Too many attempts with invalid links. Please wait a while and try again.'
  }
  // A malformed token fails validation on the check; on submit, 422 means a password rule.
  if (error.status === 422 && malformedIs422) return 'This link is not valid.'
  return null
}

function Done({ text }: { text: string }) {
  const ref = useRef<HTMLParagraphElement>(null)
  useEffect(() => {
    // The form is gone; move focus to the confirmation so it is read out.
    ref.current?.focus()
  }, [])
  return (
    <div className={`space-y-5 ${authCardClass}`}>
      <p ref={ref} tabIndex={-1} className="text-slate-700 focus:outline-none">
        {text}
      </p>
      <Link to="/login" className={authButtonClass}>
        Go to sign in
      </Link>
    </div>
  )
}

interface FieldErrors {
  password?: string | undefined
  confirm?: string | undefined
}

function PasswordForm({
  account,
  greeting,
  intro,
  submitLabel,
  pending,
  onSubmit,
}: {
  account: TokenInfo
  greeting: string
  intro: ReactNode
  submitLabel: string
  pending: boolean
  onSubmit: (password: string, handlers: { onError: (error: Error) => void }) => void
}) {
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [errors, setErrors] = useState<FieldErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const passwordRef = useRef<HTMLInputElement>(null)
  const confirmRef = useRef<HTMLInputElement>(null)
  const hintId = useId()
  const passwordErrorId = useId()
  const confirmErrorId = useId()

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    setFormError(null)
    const found: FieldErrors = {}
    if (Array.from(password).length < MIN_PASSWORD_LENGTH) {
      found.password = `Use at least ${MIN_PASSWORD_LENGTH} characters.`
    }
    if (confirm !== password) found.confirm = 'The passwords do not match.'
    setErrors(found)
    if (found.password) {
      passwordRef.current?.focus()
      return
    }
    if (found.confirm) {
      confirmRef.current?.focus()
      return
    }
    onSubmit(password, {
      onError: (error) => {
        if (
          error instanceof ApiError &&
          error.status === 422 &&
          error.code?.startsWith('password_')
        ) {
          // A server rule (common password, contains the username, ...): its message says which.
          setErrors({ password: error.message })
          passwordRef.current?.focus()
        } else if (!linkProblem(error)) {
          setFormError('Something went wrong. Please try again.')
        }
      },
    })
  }

  return (
    <form onSubmit={handleSubmit} noValidate className={`space-y-5 ${authCardClass}`}>
      <div>
        <h2 className="text-lg font-semibold text-slate-900">{greeting}</h2>
        <p className="mt-1 text-sm text-slate-600">{intro}</p>
        <p className="mt-3 text-sm text-slate-700">
          Username: <span className="font-medium text-slate-900">{account.username}</span>
        </p>
        {/* Lets password managers save the new password under the right username. */}
        <input
          type="text"
          name="username"
          autoComplete="username"
          value={account.username}
          readOnly
          hidden
        />
      </div>

      <FormAlert message={formError} />

      <div className="space-y-1.5">
        <label htmlFor="new-password" className="block text-sm font-medium text-slate-700">
          New password
        </label>
        <input
          ref={passwordRef}
          id="new-password"
          name="new-password"
          type="password"
          autoComplete="new-password"
          required
          value={password}
          onChange={(e) => {
            setPassword(e.target.value)
            setErrors((current) => ({ ...current, password: undefined }))
          }}
          aria-invalid={errors.password ? true : undefined}
          aria-describedby={errors.password ? `${passwordErrorId} ${hintId}` : hintId}
          className={authInputClass}
        />
        {errors.password && (
          <p id={passwordErrorId} className="text-sm text-red-700">
            {errors.password}
          </p>
        )}
        <p id={hintId} className="text-sm text-slate-600">
          At least {MIN_PASSWORD_LENGTH} characters. Avoid common passwords and your username. A few
          unrelated words make a strong, memorable password.
        </p>
      </div>

      <div className="space-y-1.5">
        <label htmlFor="confirm-password" className="block text-sm font-medium text-slate-700">
          Confirm new password
        </label>
        <input
          ref={confirmRef}
          id="confirm-password"
          name="confirm-password"
          type="password"
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(e) => {
            setConfirm(e.target.value)
            setErrors((current) => ({ ...current, confirm: undefined }))
          }}
          aria-invalid={errors.confirm ? true : undefined}
          aria-describedby={errors.confirm ? confirmErrorId : undefined}
          className={authInputClass}
        />
        {errors.confirm && (
          <p id={confirmErrorId} className="text-sm text-red-700">
            {errors.confirm}
          </p>
        )}
      </div>

      <button type="submit" disabled={pending} className={authButtonClass}>
        {submitLabel}
      </button>
    </form>
  )
}
