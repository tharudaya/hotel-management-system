import { type SubmitEvent, useId, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'

import { ApiError } from '../api/client'
import { redirectTarget } from '../auth/currentUser'
import { useLogin } from '../auth/queries'

const inputClass =
  'block w-full rounded-md border border-slate-300 bg-white px-3 py-2.5 text-base text-slate-900 ' +
  'shadow-sm placeholder:text-slate-400 ' +
  'focus-visible:border-sky-600 focus-visible:ring-2 focus-visible:ring-sky-600/40 focus-visible:outline-none ' +
  'aria-invalid:border-red-600 disabled:bg-slate-100'

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
    <main className="flex min-h-screen items-center justify-center bg-slate-100 px-4 py-12 sm:px-6">
      <div className="w-full max-w-md">
        <div className="mb-6 text-center">
          <p className="text-sm font-semibold tracking-wide text-sky-700 uppercase">
            Hotel Management
          </p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-900">Sign in to Front Desk</h1>
        </div>

        <form
          onSubmit={handleSubmit}
          className="space-y-5 rounded-xl bg-white p-6 shadow-md ring-1 ring-slate-200 sm:p-8"
        >
          {/* Always mounted so screen readers announce changes; empty when there is no error. */}
          <div
            id={errorId}
            role="alert"
            className={
              errorMessage
                ? 'rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800'
                : 'sr-only'
            }
          >
            {errorMessage}
          </div>

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
              className={inputClass}
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
              className={inputClass}
            />
          </div>

          <button
            type="submit"
            disabled={login.isPending}
            className="flex w-full items-center justify-center rounded-md bg-sky-700 px-4 py-2.5 text-base font-medium text-white shadow-sm hover:bg-sky-800 focus-visible:ring-2 focus-visible:ring-sky-600 focus-visible:ring-offset-2 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-60"
          >
            {login.isPending ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </main>
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
