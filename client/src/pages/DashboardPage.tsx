import { useCallback } from 'react'
import { useSearchParams } from 'react-router'

import { useAuthenticatedUser } from '../auth/currentUser'
import { can, P } from '../auth/permissions'
import { buttonSecondary } from '../components/styles'
import {
  applyFilters,
  filtersFromParams,
  filtersToParams,
  NO_FILTERS,
  type QuickView,
  type RoomFilters,
} from '../dashboard/filters'
import { FilterBar } from '../dashboard/FilterBar'
import { useRooms, useSummary } from '../dashboard/queries'
import { RoomDetailPanel } from '../dashboard/RoomDetailPanel'
import { RoomGrid } from '../dashboard/RoomGrid'
import { SummaryBar } from '../dashboard/SummaryBar'
import { useDocumentTitle } from '../lib/useDocumentTitle'

const ROOM_PARAM = 'room'

function focusTile(roomId: number) {
  // After the panel closes, return focus to the tile that opened it.
  requestAnimationFrame(() => {
    document.querySelector<HTMLElement>(`[data-room-id="${roomId}"]`)?.focus()
  })
}

export function DashboardPage() {
  const user = useAuthenticatedUser()
  useDocumentTitle(can(user, P.DASHBOARD_VIEW) ? 'Rooms' : 'No dashboard access')
  if (!can(user, P.DASHBOARD_VIEW)) {
    return (
      <section aria-labelledby="no-dashboard" className="rounded-lg bg-white p-8 shadow-sm">
        <h1 id="no-dashboard" className="text-lg font-semibold text-slate-900">
          No dashboard access
        </h1>
        <p className="mt-2 text-slate-700">
          Your role ({user.role?.name ?? 'none'}) does not include the front desk dashboard. Ask an
          administrator if you need it.
        </p>
      </section>
    )
  }
  return <Dashboard />
}

function Dashboard() {
  const rooms = useRooms()
  const summary = useSummary()
  const [params, setParams] = useSearchParams()

  const filters = filtersFromParams(params)
  const selectedRoomId = Number.parseInt(params.get(ROOM_PARAM) ?? '', 10) || null

  function setFilters(next: RoomFilters) {
    setParams((prev) => filtersToParams(next, prev), { replace: true })
  }
  function setView(view: QuickView | null) {
    setFilters({ ...filters, view })
  }
  // Stable identity so memoised tiles don't re-render when unrelated state changes.
  const selectRoom = useCallback(
    (roomId: number | null) => {
      setParams((prev) => {
        const next = new URLSearchParams(prev)
        if (roomId === null) next.delete(ROOM_PARAM)
        else next.set(ROOM_PARAM, String(roomId))
        return next
      })
    },
    [setParams],
  )

  if (rooms.isPending) {
    return <p role="status">Loading rooms…</p>
  }
  if (rooms.isError) {
    return (
      <div role="alert" className="space-y-3">
        <p className="text-slate-800">Could not load the rooms.</p>
        <button
          type="button"
          onClick={() => {
            void rooms.refetch()
          }}
          className={buttonSecondary}
        >
          Retry
        </button>
      </div>
    )
  }

  const allRooms = rooms.data.rooms
  const visibleRooms = applyFilters(allRooms, filters)
  const selectedRoom = allRooms.find((r) => r.room_id === selectedRoomId) ?? null

  return (
    <div className={selectedRoom ? 'xl:pr-[28rem]' : undefined}>
      <h1 className="sr-only">Rooms</h1>
      <div className="space-y-4">
        {summary.data ? (
          <SummaryBar summary={summary.data} activeView={filters.view} onSelectView={setView} />
        ) : summary.isError ? (
          <div
            role="alert"
            className="flex items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
          >
            <span>Could not load today's summary.</span>
            <button
              type="button"
              onClick={() => {
                void summary.refetch()
              }}
              className={buttonSecondary}
            >
              Retry
            </button>
          </div>
        ) : (
          <p
            role="status"
            className="h-20 rounded-lg bg-slate-200/60 px-4 py-3 text-sm text-slate-500"
          >
            Loading today's summary…
          </p>
        )}
        <FilterBar rooms={allRooms} filters={filters} onChange={setFilters} />
        <p role="status" className="text-sm text-slate-600">
          Showing {visibleRooms.length} of {allRooms.length} rooms
        </p>
        {visibleRooms.length > 0 ? (
          <RoomGrid rooms={visibleRooms} selectedRoomId={selectedRoomId} onSelect={selectRoom} />
        ) : (
          <div className="rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center">
            <p className="text-slate-700">No rooms match these filters.</p>
            <button
              type="button"
              onClick={() => {
                setFilters(NO_FILTERS)
              }}
              className={`${buttonSecondary} mt-3`}
            >
              Clear filters
            </button>
          </div>
        )}
      </div>

      {selectedRoom && (
        <RoomDetailPanel
          room={selectedRoom}
          onClose={() => {
            selectRoom(null)
            focusTile(selectedRoom.room_id)
          }}
        />
      )}
    </div>
  )
}
