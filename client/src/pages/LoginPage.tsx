import { type SubmitEvent, useId, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'

import { ApiError } from '../api/client'
import { redirectTarget } from '../auth/currentUser'
import { useLogin } from '../auth/queries'
import {
  AuthShell,
  FormAlert,
  authButtonClass,
  authCardClass,
  authInputClass,
  authLinkClass,
} from '../components/AuthShell'

export function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const login = useLogin()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const errorId = useId()

  const errorMessage = login.isError ? loginErrorMessage(login.error) : null

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    login.mutate(
      { username, password },
      {
        onSuccess: () => {
          void navigate(redirectTarget(location.state), { replace: true })
        },
      },
    )
  }

  return (
    <AuthShell
      title="Sign in"
      footer={
        <Link to="/forgot-password" className={authLinkClass}>
          Forgot your password?
        </Link>
      }
    >
      <form onSubmit={handleSubmit} className={`space-y-5 ${authCardClass}`}>
        <FormAlert id={errorId} message={errorMessage} />

        <div className="space-y-1.5">
          <label htmlFor="username" className="block text-sm font-medium text-slate-700">
            Username
          </label>
          <input
            id="username"
            name="username"
            type="text"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            required
            value={username}
            onChange={(e) => {
              setUsername(e.target.value)
            }}
            aria-invalid={errorMessage ? true : undefined}
            aria-describedby={errorMessage ? errorId : undefined}
            className={authInputClass}
          />
        </div>

        <div className="space-y-1.5">
          <label htmlFor="password" className="block text-sm font-medium text-slate-700">
            Password
          </label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => {
              setPassword(e.target.value)
            }}
            aria-invalid={errorMessage ? true : undefined}
            aria-describedby={errorMessage ? errorId : undefined}
            className={authInputClass}
          />
        </div>

        <button type="submit" disabled={login.isPending} className={authButtonClass}>
          {login.isPending ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </AuthShell>
  )
}

/** Deliberately generic: never reveal whether the username exists. */
function loginErrorMessage(error: Error): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return 'Invalid username or password.'
    if (error.status === 429)
      return 'Too many failed attempts. Please wait a few minutes and try again.'
  }
  return 'Sign in failed. Please try again.'
}
