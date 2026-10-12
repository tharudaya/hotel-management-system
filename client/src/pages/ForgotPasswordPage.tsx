import { type SubmitEvent, useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router'

import { ApiError } from '../api/client'
import { useForgotPassword } from '../account/queries'
import {
  AuthShell,
  FormAlert,
  authButtonClass,
  authCardClass,
  authInputClass,
  authLinkClass,
} from '../components/AuthShell'

const backToSignIn = (
  <Link to="/login" className={authLinkClass}>
    Back to sign in
  </Link>
)

/** /forgot-password. The server answers the same whether or not the account exists. */
export function ForgotPasswordPage() {
  const forgot = useForgotPassword()
  const [email, setEmail] = useState('')
  const [fieldError, setFieldError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const hintId = useId()
  const errorId = useId()

  const formError =
    forgot.isError && !isInvalidEmail(forgot.error)
      ? 'Something went wrong. Please try again.'
      : null

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    const value = email.trim()
    if (!/^[^\s@]+@[^\s@]+$/.test(value)) {
      setFieldError('Enter a valid email address, like name@example.com.')
      inputRef.current?.focus()
      return
    }
    setFieldError(null)
    forgot.mutate(
      { email: value },
      {
        onError: (error) => {
          if (isInvalidEmail(error)) {
            setFieldError('Enter a valid email address, like name@example.com.')
            inputRef.current?.focus()
          }
        },
      },
    )
  }

  if (forgot.isSuccess) {
    return (
      <AuthShell title="Check your email" footer={backToSignIn}>
        <Sent message={forgot.data.detail} />
      </AuthShell>
    )
  }

  return (
    <AuthShell title="Reset your password" footer={backToSignIn}>
      <form onSubmit={handleSubmit} noValidate className={`space-y-5 ${authCardClass}`}>
        <FormAlert message={formError} />

        <div className="space-y-1.5">
          <label htmlFor="email" className="block text-sm font-medium text-slate-700">
            Email address
          </label>
          <input
            ref={inputRef}
            id="email"
            name="email"
            type="email"
            autoComplete="email"
            autoCapitalize="none"
            spellCheck={false}
            required
            value={email}
            onChange={(e) => {
              setEmail(e.target.value)
              setFieldError(null)
            }}
            aria-invalid={fieldError ? true : undefined}
            aria-describedby={fieldError ? `${errorId} ${hintId}` : hintId}
            className={authInputClass}
          />
          {fieldError && (
            <p id={errorId} className="text-sm text-red-700">
              {fieldError}
            </p>
          )}
          <p id={hintId} className="text-sm text-slate-600">
            We will email you a link to choose a new password. No email on your account? Ask your
            manager to reset it.
          </p>
        </div>

        <button type="submit" disabled={forgot.isPending} className={authButtonClass}>
          {forgot.isPending ? 'Sending…' : 'Send reset link'}
        </button>
      </form>
    </AuthShell>
  )
}

function isInvalidEmail(error: Error): boolean {
  return error instanceof ApiError && error.status === 422
}

function Sent({ message }: { message: string }) {
  const ref = useRef<HTMLParagraphElement>(null)
  useEffect(() => {
    ref.current?.focus()
  }, [])
  return (
    <div className={`space-y-3 ${authCardClass}`}>
      <p ref={ref} tabIndex={-1} className="text-slate-700 focus:outline-none">
        {message}
      </p>
      <p className="text-sm text-slate-600">
        The link works once and expires in 1 hour. Nothing arrived? Check your spam folder, or ask
        your manager to reset your password.
      </p>
    </div>
  )
}
