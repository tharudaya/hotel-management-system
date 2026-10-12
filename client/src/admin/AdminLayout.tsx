import { Navigate, NavLink, Outlet } from 'react-router'

import { Can } from '../auth/Can'
import { NoAccess } from '../auth/RequirePermission'
import { usePermissions } from '../auth/usePermissions'
import { focusRing } from '../components/styles'
import { ADMIN_SECTIONS } from './sections'

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `block rounded-md px-3 py-2 text-sm font-medium ${focusRing} ${
    isActive ? 'bg-sky-50 text-sky-800' : 'text-slate-700 hover:bg-slate-50 hover:text-slate-900'
  }`

/** /admin: side navigation (only the sections the user may see) and the section. */
export function AdminLayout() {
  return (
    <div className="flex flex-col gap-6 md:flex-row">
      <nav aria-label="Admin" className="md:w-52 md:shrink-0">
        <p className="mb-2 px-3 text-xs font-semibold tracking-wide text-slate-500 uppercase">
          Admin
        </p>
        <ul className="flex gap-1 rounded-lg bg-white p-2 shadow-sm md:flex-col">
          {ADMIN_SECTIONS.map((section) => (
            <Can key={section.path} permission={section.permission}>
              <li>
                <NavLink to={`/admin/${section.path}`} className={linkClass}>
                  {section.label}
                </NavLink>
              </li>
            </Can>
          ))}
        </ul>
      </nav>
      <div className="min-w-0 flex-1">
        <Outlet />
      </div>
    </div>
  )
}

/** /admin itself: open the first section the user may see. */
export function AdminIndex() {
  const { can } = usePermissions()
  const first = ADMIN_SECTIONS.find((section) => can(section.permission))
  return first ? <Navigate to={`/admin/${first.path}`} replace /> : <NoAccess />
}
