import { NavLink, Outlet } from 'react-router'

import { focusRing } from '../../components/styles'

const tabClass = ({ isActive }: { isActive: boolean }) =>
  `-mb-px border-b-2 px-3 py-2 text-sm font-medium ${focusRing} ${
    isActive
      ? 'border-sky-700 text-sky-800'
      : 'border-transparent text-slate-600 hover:border-slate-300 hover:text-slate-900'
  }`

/** /admin/rooms: the Rooms and Room types pages under one heading. */
export function RoomsLayout() {
  return (
    <div className="space-y-4">
      <nav aria-label="Room setup" className="border-b border-slate-200">
        <ul className="flex gap-2">
          <li>
            <NavLink to="/admin/rooms" end className={tabClass}>
              Rooms
            </NavLink>
          </li>
          <li>
            <NavLink to="/admin/rooms/types" className={tabClass}>
              Room types
            </NavLink>
          </li>
        </ul>
      </nav>
      <Outlet />
    </div>
  )
}
