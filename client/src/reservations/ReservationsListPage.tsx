import { Plus, Star } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router'

import type { ReservationListItem } from '../api/types'
import { Can } from '../auth/Can'
import { P } from '../auth/permissions'
import { buttonPrimary, buttonSecondary, fieldClass, focusRing } from '../components/styles'
import { Pagination, SortHeader } from '../components/table'
import { formatDate, plural } from '../lib/format'
import { useDocumentTitle } from '../lib/useDocumentTitle'
import {
  DATE_FIELD_LABEL,
  DATE_FIELDS,
  hasFilters,
  NO_FILTERS,
  PAGE_SIZE,
  parseParams,
  type ReservationListParams,
  type Sort,
  type SortField,
  toSearch,
  VIEW_LABEL,
  VIEWS,
} from './params'
import { useReservationList } from './queries'
import { RESERVATION_STATUS, RESERVATION_STATUSES, SOURCE_LABEL } from './status'
import { ReservationStatusBadge } from './StatusBadge'

const SEARCH_DEBOUNCE_MS = 300

/** /reservations: search, filters, today's views, sort and pages; all state in the URL. */
export function ReservationsListPage() {
  useDocumentTitle('Reservations')
  const [search, setSearch] = useSearchParams()
  const params = parseParams(search)
  const list = useReservationList(params)

  function update(changes: Partial<ReservationListParams>) {
    // Any change other than paging starts again at page 1.
    const next = { ...params, page: 1, ...changes }
    setSearch(toSearch(next), { replace: !('page' in changes) && 'q' in changes })
  }

  return (
    <section aria-labelledby="reservations-heading" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 id="reservations-heading" className="text-xl font-semibold text-slate-900">
          Reservations
        </h1>
        <Can permission={P.RESERVATION_CREATE}>
          <Link to="/reservations/new" className={buttonPrimary}>
            <Plus aria-hidden="true" className="size-4" />
            New reservation
          </Link>
        </Can>
      </div>

      <QuickViews params={params} onChange={update} />
      <Filters params={params} onChange={update} />

      <div className="rounded-lg bg-white shadow-sm">
        <Results
          params={params}
          list={list}
          onSort={(sort) => {
            update({ sort })
          }}
          onClearFilters={() => {
            update(NO_FILTERS)
          }}
        />
      </div>

      {list.data && list.data.total > PAGE_SIZE && (
        <Pagination
          page={params.page}
          total={list.data.total}
          pageSize={PAGE_SIZE}
          onPage={(page) => {
            update({ page })
          }}
        />
      )}
    </section>
  )
}

/** Toggle buttons for today's views; one at a time. */
function QuickViews({
  params,
  onChange,
}: {
  params: ReservationListParams
  onChange: (changes: Partial<ReservationListParams>) => void
}) {
  return (
    <div role="group" aria-label="Today" className="flex flex-wrap gap-2">
      {VIEWS.map((view) => {
        const active = params.view === view
        return (
          <button
            key={view}
            type="button"
            aria-pressed={active}
            onClick={() => {
              onChange({ view: active ? null : view })
            }}
            className={`rounded-full border px-3 py-1.5 text-sm font-medium ${focusRing} ${
              active
                ? 'border-sky-700 bg-sky-700 text-white'
                : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-50'
            }`}
          >
            {VIEW_LABEL[view]}
          </button>
        )
      })}
    </div>
  )
}

function Filters({
  params,
  onChange,
}: {
  params: ReservationListParams
  onChange: (changes: Partial<ReservationListParams>) => void
}) {
  const [text, setText] = useState(params.q)
  const searchId = useId()
  const statusId = useId()
  const datesId = useId()
  const fromId = useId()
  const toId = useId()
  const onChangeRef = useRef(onChange)
  useEffect(() => {
    onChangeRef.current = onChange
  })

  // The URL changed from elsewhere (back button, "Clear filters"): show it.
  const [lastUrlQ, setLastUrlQ] = useState(params.q)
  if (params.q !== lastUrlQ) {
    setLastUrlQ(params.q)
    setText(params.q)
  }

  // Debounce typing into the URL.
  useEffect(() => {
    if (text === params.q) return
    const timer = setTimeout(() => {
      onChangeRef.current({ q: text })
    }, SEARCH_DEBOUNCE_MS)
    return () => {
      clearTimeout(timer)
    }
  }, [text, params.q])

  return (
    <form
      role="search"
      aria-label="Filter reservations"
      onSubmit={(e) => {
        e.preventDefault()
        onChange({ q: text }) // Enter searches at once
      }}
      className="grid gap-3 rounded-lg bg-white p-4 shadow-sm sm:grid-cols-2 lg:grid-cols-[2fr_1fr_1fr_1fr_1fr]"
    >
      <div className="space-y-1">
        <label htmlFor={searchId} className="block text-sm font-medium text-slate-700">
          Search
        </label>
        <input
          id={searchId}
          type="search"
          value={text}
          maxLength={100}
          placeholder="Confirmation code or guest name"
          onChange={(e) => {
            setText(e.target.value)
          }}
          className={fieldClass}
        />
      </div>
      <div className="space-y-1">
        <label htmlFor={statusId} className="block text-sm font-medium text-slate-700">
          Status
        </label>
        <select
          id={statusId}
          value={params.status ?? ''}
          onChange={(e) => {
            const value = e.target.value
            onChange({ status: RESERVATION_STATUSES.find((s) => s === value) ?? null })
          }}
          className={fieldClass}
        >
          <option value="">All statuses</option>
          {RESERVATION_STATUSES.map((status) => (
            <option key={status} value={status}>
              {RESERVATION_STATUS[status].label}
            </option>
          ))}
        </select>
      </div>
      <div className="space-y-1">
        <label htmlFor={datesId} className="block text-sm font-medium text-slate-700">
          Dates apply to
        </label>
        <select
          id={datesId}
          value={params.dates}
          onChange={(e) => {
            const value = e.target.value
            onChange({ dates: DATE_FIELDS.find((d) => d === value) ?? 'staying' })
          }}
          className={fieldClass}
        >
          {DATE_FIELDS.map((field) => (
            <option key={field} value={field}>
              {DATE_FIELD_LABEL[field]}
            </option>
          ))}
        </select>
      </div>
      <div className="space-y-1">
        <label htmlFor={fromId} className="block text-sm font-medium text-slate-700">
          From
        </label>
        <input
          id={fromId}
          type="date"
          value={params.from}
          onChange={(e) => {
            onChange({ from: e.target.value })
          }}
          className={fieldClass}
        />
      </div>
      <div className="space-y-1">
        <label htmlFor={toId} className="block text-sm font-medium text-slate-700">
          To
        </label>
        <input
          id={toId}
          type="date"
          value={params.to}
          onChange={(e) => {
            onChange({ to: e.target.value })
          }}
          className={fieldClass}
        />
      </div>
    </form>
  )
}

const COLUMNS: { label: string; sort?: SortField }[] = [
  { label: 'Code', sort: 'confirmation_code' },
  { label: 'Guest', sort: 'guest' },
  { label: 'Arrival', sort: 'check_in_date' },
  { label: 'Departure', sort: 'check_out_date' },
  { label: 'Nights' },
  { label: 'Room type' },
  { label: 'Room' },
  { label: 'Status' },
  { label: 'Source' },
]

const SORT_NAMES: Record<SortField, string> = {
  confirmation_code: 'confirmation code',
  guest: 'guest name',
  check_in_date: 'arrival',
  check_out_date: 'departure',
}

function sortDescription(sort: Sort): string {
  const descending = sort.startsWith('-')
  const field = (descending ? sort.slice(1) : sort) as SortField
  return `${SORT_NAMES[field]}, ${descending ? 'descending' : 'ascending'}`
}

function Results({
  params,
  list,
  onSort,
  onClearFilters,
}: {
  params: ReservationListParams
  list: ReturnType<typeof useReservationList>
  onSort: (sort: Sort) => void
  onClearFilters: () => void
}) {
  const location = useLocation()

  if (list.isPending) {
    return (
      <p role="status" className="p-6 text-slate-600">
        Loading reservations…
      </p>
    )
  }
  if (list.isError) {
    return (
      <div role="alert" className="space-y-3 p-6">
        <p className="text-slate-800">The reservations could not be loaded.</p>
        <button
          type="button"
          onClick={() => {
            void list.refetch()
          }}
          className={buttonSecondary}
        >
          Try again
        </button>
      </div>
    )
  }

  const { items, total, page } = list.data
  const first = (page - 1) * PAGE_SIZE + 1
  const filtered = hasFilters(params)

  return (
    <>
      {/* Announces result changes after searching or filtering. */}
      <p role="status" className="border-b border-slate-200 px-4 py-3 text-sm text-slate-600">
        {total === 0
          ? 'No reservations found'
          : `${plural(total, 'reservation')}${total > PAGE_SIZE ? `, showing ${first}–${first + items.length - 1}` : ''}`}
        {list.isPlaceholderData && ' (updating…)'}
      </p>
      {items.length === 0 ? (
        <div className="space-y-3 p-6 text-slate-700">
          <p>
            {filtered ? 'No reservations match these filters.' : 'There are no reservations yet.'}
          </p>
          {filtered && (
            <button type="button" onClick={onClearFilters} className={buttonSecondary}>
              Clear filters
            </button>
          )}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">
              Reservations, sorted by {sortDescription(params.sort)}
            </caption>
            <thead className="bg-slate-50 text-slate-700">
              <tr>
                {COLUMNS.map((column) => (
                  <SortHeader
                    key={column.label}
                    label={column.label}
                    field={column.sort}
                    sort={params.sort}
                    onSort={onSort}
                  />
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((reservation) => (
                <ReservationRow
                  key={reservation.id}
                  reservation={reservation}
                  listSearch={location.search}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

function ReservationRow({
  reservation: r,
  listSearch,
}: {
  reservation: ReservationListItem
  listSearch: string
}) {
  return (
    <tr className="hover:bg-slate-50">
      <td className="px-4 py-2 font-mono whitespace-nowrap text-slate-800">
        {r.confirmation_code}
      </td>
      <th scope="row" className="px-4 py-2 font-medium">
        <Link
          to={`/reservations/${r.id}`}
          state={{ listSearch }}
          className={`inline-flex items-center gap-1 rounded-sm text-sky-800 hover:underline ${focusRing}`}
        >
          {r.guest_name}
          {r.is_vip && (
            <span className="inline-flex items-center gap-0.5 rounded bg-amber-400 px-1 text-xs font-bold text-amber-950">
              <Star aria-hidden className="size-3 fill-current" />
              VIP
            </span>
          )}
        </Link>
      </th>
      <td className="px-4 py-2 whitespace-nowrap text-slate-700">
        <time dateTime={r.check_in_date}>{formatDate(r.check_in_date)}</time>
      </td>
      <td className="px-4 py-2 whitespace-nowrap text-slate-700">
        <time dateTime={r.check_out_date}>{formatDate(r.check_out_date)}</time>
      </td>
      <td className="px-4 py-2 text-slate-700">{r.nights}</td>
      <td className="px-4 py-2 text-slate-700">{r.room_type_name}</td>
      <td className="px-4 py-2 text-slate-700">
        {r.room_number ?? <span className="text-slate-400">Not assigned</span>}
      </td>
      <td className="px-4 py-2">
        <ReservationStatusBadge status={r.status} />
      </td>
      <td className="px-4 py-2 text-slate-700">{SOURCE_LABEL[r.source]}</td>
    </tr>
  )
}
