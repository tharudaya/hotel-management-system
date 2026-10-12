import { isRouteErrorResponse, Link, useRouteError } from 'react-router'

import { buttonPrimary } from './styles'

interface ErrorPageProps {
  /** Render as "page not found" without a thrown error (catch-all route). */
  notFound?: boolean
}

/**
 * Shown for unknown URLs and for errors thrown while rendering a route, instead of
 * React Router's developer error screen. Never shows error details to the user.
 */
export function ErrorPage({ notFound = false }: ErrorPageProps) {
  return notFound ? <ErrorLayout missing /> : <RouteError />
}

function RouteError() {
  const error = useRouteError()
  const missing = isRouteErrorResponse(error) && error.status === 404
  if (!missing) console.error(error)
  return <ErrorLayout missing={missing} />
}

function ErrorLayout({ missing }: { missing: boolean }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-100 px-4">
      <div className="max-w-md rounded-xl bg-white p-8 text-center shadow-md ring-1 ring-slate-200">
        <h1 className="text-xl font-semibold text-slate-900">
          {missing ? 'Page not found' : 'Something went wrong'}
        </h1>
        <p className="mt-2 text-slate-600">
          {missing
            ? 'There is nothing at this address.'
            : 'An unexpected error occurred. Reloading the page usually helps.'}
        </p>
        <Link to="/" className={`${buttonPrimary} mt-6`}>
          Go to the dashboard
        </Link>
      </div>
    </main>
  )
}
