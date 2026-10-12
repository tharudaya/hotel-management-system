import { Navigate, Outlet, useLocation } from 'react-router'

import { ApiError } from '../api/client'
import { CurrentUserContext } from './currentUser'
import { useCurrentUser } from './queries'

/**
 * Renders child routes only for a logged-in user; otherwise redirects to /login,
 * remembering the requested page so login can return there.
 *
 * A UI convenience only: the server enforces authentication and roles on every request.
 */
export function ProtectedRoute() {
  const location = useLocation()
  const { data: user, error, isPending, refetch, isRefetching } = useCurrentUser()

  if (isPending) {
    return (
      <div role="status" className="flex min-h-screen items-center justify-center text-slate-500">
        Loading…
      </div>
    )
  }

  if (error) {
    if (error instanceof ApiError && error.status === 401) {
      const from = `${location.pathname}${location.search}${location.hash}`
      return <Navigate to="/login" replace state={{ from }} />
    }
    // Server unreachable or failing: not a reason to send the user to the login page.
    return (
      <div className="flex min-h-screen items-center justify-center px-4">
        <div role="alert" className="max-w-sm text-center">
          <p className="text-slate-800">Could not reach the server.</p>
          <button
            type="button"
            disabled={isRefetching}
            onClick={() => {
              void refetch()
            }}
            className="mt-4 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-sky-600 focus-visible:ring-offset-2 focus-visible:outline-none disabled:opacity-60"
          >
            {isRefetching ? 'Retrying…' : 'Retry'}
          </button>
        </div>
      </div>
    )
  }

  return (
    <CurrentUserContext value={user}>
      <Outlet />
    </CurrentUserContext>
  )
}
