import type { ReactNode } from 'react'
import { Link, Outlet } from 'react-router'

import { focusRing } from '../components/styles'
import { P, type PermissionKey } from './permissions'
import { usePermissions } from './usePermissions'
import { useDocumentTitle } from '../lib/useDocumentTitle'

type RequirePermissionProps = {
  /** Rendered when allowed; defaults to the child routes. */
  children?: ReactNode
} & (
  | { permission: PermissionKey; anyOf?: never }
  | { anyOf: readonly PermissionKey[]; permission?: never }
)

/**
 * Route guard for signed-in pages: without the permission, show a "no access" page
 * (the user *is* signed in, so sending them to the login page would be wrong).
 * Use inside ProtectedRoute. UI only: the server enforces every request.
 */
export function RequirePermission({ children, ...rule }: RequirePermissionProps) {
  const { can, canAny } = usePermissions()
  const allowed = rule.permission !== undefined ? can(rule.permission) : canAny(...rule.anyOf)
  if (!allowed) return <NoAccess />
  return children ?? <Outlet />
}

export function NoAccess() {
  useDocumentTitle('No access')
  const { can } = usePermissions()
  return (
    <section
      aria-labelledby="no-access"
      className="mx-auto max-w-lg rounded-lg bg-white p-8 text-center shadow-sm"
    >
      <h1 id="no-access" className="text-lg font-semibold text-slate-900">
        You do not have access
      </h1>
      <p className="mt-2 text-slate-700">
        Your role does not include this page. If you need it, ask an administrator.
      </p>
      {can(P.DASHBOARD_VIEW) && (
        <Link
          to="/"
          className={`mt-4 inline-block rounded-sm font-medium text-sky-700 hover:underline ${focusRing}`}
        >
          Back to the dashboard
        </Link>
      )}
    </section>
  )
}
