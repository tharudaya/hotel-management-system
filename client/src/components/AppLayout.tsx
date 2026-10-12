import { Outlet, useNavigate } from 'react-router'

import { useAuthenticatedUser } from '../auth/currentUser'
import { can, P } from '../auth/permissions'
import { useLogout } from '../auth/queries'
import { useHotelInfo } from '../dashboard/queries'
import { formatDate } from '../lib/format'
import { ConnectionIndicator } from '../realtime/ConnectionIndicator'
import { useRealtime } from '../realtime/useRealtime'
import { buttonSecondary } from './styles'

/** Shell for signed-in pages: header with the user and a sign-out button. */
export function AppLayout() {
  const user = useAuthenticatedUser()
  const navigate = useNavigate()
  const logout = useLogout()
  const hotel = useHotelInfo()
  const realtimeStatus = useRealtime(can(user, P.DASHBOARD_VIEW))

  function handleLogout() {
    logout.mutate(undefined, {
      // Leave even if the request failed: the local session state is already cleared.
      onSettled: () => {
        void navigate('/login', { replace: true })
      },
    })
  }

  return (
    <div className="min-h-screen bg-slate-100">
      <a
        href="#main"
        onClick={(e) => {
          // Focus explicitly: not every browser moves focus for in-page links.
          e.preventDefault()
          document.getElementById('main')?.focus()
        }}
        className="sr-only rounded-md bg-white px-4 py-2 font-medium text-sky-800 shadow focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-30"
      >
        Skip to main content
      </a>
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <div>
            <p className="text-lg leading-tight font-semibold text-slate-900">
              {hotel.data?.name ?? 'Front Desk'}
            </p>
            {hotel.data && (
              <p className="text-sm text-slate-600">
                Business date{' '}
                <time dateTime={hotel.data.business_date}>
                  {formatDate(hotel.data.business_date)}
                </time>
              </p>
            )}
          </div>
          <div className="flex items-center gap-3 sm:gap-4">
            {realtimeStatus && <ConnectionIndicator status={realtimeStatus} />}
            <p className="text-right text-sm leading-tight">
              <span className="block font-medium text-slate-900">{user.full_name}</span>
              <span className="block text-slate-500">{user.role?.name ?? 'No role'}</span>
            </p>
            <button
              type="button"
              onClick={handleLogout}
              disabled={logout.isPending}
              className={buttonSecondary}
            >
              {logout.isPending ? 'Signing out…' : 'Sign out'}
            </button>
          </div>
        </div>
      </header>
      <main
        id="main"
        tabIndex={-1}
        className="mx-auto max-w-7xl px-4 py-6 focus:outline-none sm:px-6"
      >
        <Outlet />
      </main>
    </div>
  )
}
