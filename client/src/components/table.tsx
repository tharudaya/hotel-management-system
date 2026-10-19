/** Pieces shared by the paginated, sortable lists (users, reservations). */
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react'

import { buttonSecondary, focusRing } from './styles'

/** "name" sorts ascending, "-name" descending. */
export type SortValue<F extends string> = F | `-${F}`

/**
 * A column header. Sortable when `field` is given: a button that toggles ascending and
 * descending, with aria-sort on the header cell.
 */
export function SortHeader<F extends string>({
  label,
  field,
  sort,
  onSort,
}: {
  label: string
  field?: F | undefined
  sort: SortValue<F>
  onSort: (sort: SortValue<F>) => void
}) {
  if (!field) {
    return (
      <th scope="col" className="px-4 py-2 font-semibold">
        {label}
      </th>
    )
  }
  const active = sort === field || sort === `-${field}`
  const descending = active && sort.startsWith('-')
  const Icon = !active ? ArrowUpDown : descending ? ArrowDown : ArrowUp
  return (
    <th
      scope="col"
      aria-sort={active ? (descending ? 'descending' : 'ascending') : undefined}
      className="px-2 py-1 font-semibold"
    >
      <button
        type="button"
        onClick={() => {
          onSort(active && !descending ? `-${field}` : field)
        }}
        className={`inline-flex items-center gap-1 rounded px-2 py-1 hover:bg-slate-100 ${focusRing}`}
      >
        {label}
        <Icon aria-hidden="true" className={`size-3.5 ${active ? '' : 'text-slate-400'}`} />
      </button>
    </th>
  )
}

export function Pagination({
  page,
  total,
  pageSize,
  onPage,
}: {
  page: number
  total: number
  pageSize: number
  onPage: (page: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  return (
    <nav aria-label="Pagination" className="flex items-center justify-between gap-3">
      <button
        type="button"
        disabled={page <= 1}
        onClick={() => {
          onPage(page - 1)
        }}
        className={buttonSecondary}
      >
        Previous
      </button>
      <p className="text-sm text-slate-700">
        Page {page} of {pages}
      </p>
      <button
        type="button"
        disabled={page >= pages}
        onClick={() => {
          onPage(page + 1)
        }}
        className={buttonSecondary}
      >
        Next
      </button>
    </nav>
  )
}
