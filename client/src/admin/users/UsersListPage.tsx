import { ArrowDown, ArrowUp, ArrowUpDown, Plus } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router'

import type { UserListItem } from '../../api/types'
import { Can } from '../../auth/Can'
import { useAuthenticatedUser } from '../../auth/currentUser'
import { P } from '../../auth/permissions'
import { buttonPrimary, buttonSecondary, fieldClass, focusRing } from '../../components/styles'
import { formatDateTime, plural } from '../../lib/format'
import {
  hasFilters,
  PAGE_SIZE,
  parseParams,
  type Sort,
  type SortField,
  toSearch,
  USER_STATUSES,
  type UserListParams,
} from './params'
import { useAssignableRoles, useUserList } from './queries'
import { StatusBadge } from './StatusBadge'
import { USER_STATUS } from './status'
import { useDocumentTitle } from '../../lib/useDocumentTitle'

const SEARCH_DEBOUNCE_MS = 300

/** /admin/users: searchable, filterable, sortable, paginated; all state in the URL. */
export function UsersListPage() {
  useDocumentTitle('Users')
  const [search, setSearch] = useSearchParams()
  const params = parseParams(search)
  const list = useUserList(params)

  function update(changes: Partial<UserListParams>) {
    // Any change other than paging starts again at page 1.
    const next = { ...params, page: 1, ...changes }
    setSearch(toSearch(next), { replace: !('page' in changes) && 'q' in changes })
  }

  return (
    <section aria-labelledby="users-heading" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 id="users-heading" className="text-xl font-semibold text-slate-900">
          Users
        </h1>
        <Can permission={P.USER_MANAGE}>
          <Link to="/admin/users/new" className={buttonPrimary}>
            <Plus aria-hidden="true" className="size-4" />
            Add user
          </Link>
        </Can>
      </div>

      <Filters params={params} onChange={update} />

      <div className="rounded-lg bg-white shadow-sm">
        <Results
          params={params}
          list={list}
          onSort={(sort) => {
            update({ sort })
          }}
          onClearFilters={() => {
            update({ q: '', roleId: null, status: null })
          }}
        />
      </div>

      {list.data && list.data.total > PAGE_SIZE && (
        <Pagination
          page={params.page}
          total={list.data.total}
          onPage={(page) => {
            update({ page })
          }}
        />
      )}
    </section>
  )
}

function Filters({
  params,
  onChange,
}: {
  params: UserListParams
  onChange: (changes: Partial<UserListParams>) => void
}) {
  const roles = useAssignableRoles()
  const [text, setText] = useState(params.q)
  const searchId = useId()
  const roleId = useId()
  const statusId = useId()
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
      aria-label="Filter users"
      onSubmit={(e) => {
        e.preventDefault()
        onChange({ q: text }) // Enter searches at once
      }}
      className="grid gap-3 rounded-lg bg-white p-4 shadow-sm sm:grid-cols-[2fr_1fr_1fr]"
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
          placeholder="Name, username, email or employee ID"
          onChange={(e) => {
            setText(e.target.value)
          }}
          className={fieldClass}
        />
      </div>
      <div className="space-y-1">
        <label htmlFor={roleId} className="block text-sm font-medium text-slate-700">
          Role
        </label>
        <select
          id={roleId}
          value={params.roleId ?? ''}
          onChange={(e) => {
            onChange({ roleId: e.target.value ? Number(e.target.value) : null })
          }}
          className={fieldClass}
        >
          <option value="">All roles</option>
          {roles.data?.map((role) => (
            <option key={role.id} value={role.id}>
              {role.name}
            </option>
          ))}
        </select>
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
            onChange({ status: USER_STATUSES.find((s) => s === value) ?? null })
          }}
          className={fieldClass}
        >
          <option value="">All statuses</option>
          {USER_STATUSES.map((status) => (
            <option key={status} value={status}>
              {USER_STATUS[status].label}
            </option>
          ))}
        </select>
      </div>
    </form>
  )
}

const COLUMNS: { label: string; sort?: SortField }[] = [
  { label: 'Name', sort: 'name' },
  { label: 'Username', sort: 'username' },
  { label: 'Email', sort: 'email' },
  { label: 'Role' },
  { label: 'Status' },
  { label: 'Last login', sort: 'last_login_at' },
]

function Results({
  params,
  list,
  onSort,
  onClearFilters,
}: {
  params: UserListParams
  list: ReturnType<typeof useUserList>
  onSort: (sort: Sort) => void
  onClearFilters: () => void
}) {
  const location = useLocation()
  const { property } = useAuthenticatedUser()

  if (list.isPending) {
    return (
      <p role="status" className="p-6 text-slate-600">
        Loading users…
      </p>
    )
  }
  if (list.isError) {
    return (
      <div role="alert" className="space-y-3 p-6">
        <p className="text-slate-800">The users could not be loaded.</p>
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
          ? 'No users found'
          : `${plural(total, 'user')}${total > PAGE_SIZE ? `, showing ${first}–${first + items.length - 1}` : ''}`}
        {list.isPlaceholderData && ' (updating…)'}
      </p>
      {items.length === 0 ? (
        <div className="space-y-3 p-6 text-slate-700">
          <p>{filtered ? 'No users match these filters.' : 'There are no users yet.'}</p>
          {filtered && (
            <button type="button" onClick={onClearFilters} className={buttonSecondary}>
              Clear filters
            </button>
          )}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Users, sorted by {sortDescription(params.sort)}</caption>
            <thead className="bg-slate-50 text-slate-700">
              <tr>
                {COLUMNS.map((column) => (
                  <HeaderCell
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
              {items.map((user) => (
                <UserRow
                  key={user.id}
                  user={user}
                  timeZone={property.time_zone}
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

const SORT_NAMES: Record<SortField, string> = {
  name: 'name',
  username: 'username',
  email: 'email',
  last_login_at: 'last login',
}

function sortDescription(sort: Sort): string {
  const descending = sort.startsWith('-')
  const field = (descending ? sort.slice(1) : sort) as SortField
  return `${SORT_NAMES[field]}, ${descending ? 'descending' : 'ascending'}`
}

function HeaderCell({
  label,
  field,
  sort,
  onSort,
}: {
  label: string
  field: SortField | undefined
  sort: Sort
  onSort: (sort: Sort) => void
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

function UserRow({
  user,
  timeZone,
  listSearch,
}: {
  user: UserListItem
  timeZone: string
  listSearch: string
}) {
  return (
    <tr className="hover:bg-slate-50">
      <th scope="row" className="px-4 py-2 font-medium">
        <Link
          to={`/admin/users/${user.id}`}
          state={{ listSearch }}
          className={`rounded-sm text-sky-800 hover:underline ${focusRing}`}
        >
          {user.full_name}
        </Link>
      </th>
      <td className="px-4 py-2 text-slate-700">{user.username}</td>
      <td className="px-4 py-2 text-slate-700">
        {user.email ?? <span className="text-slate-400">None</span>}
      </td>
      <td className="px-4 py-2 text-slate-700">
        {user.role?.name ?? <span className="text-slate-400">No role</span>}
      </td>
      <td className="px-4 py-2">
        <StatusBadge status={user.status} />
      </td>
      <td className="px-4 py-2 whitespace-nowrap text-slate-700">
        {user.last_login_at ? (
          <time dateTime={user.last_login_at}>{formatDateTime(user.last_login_at, timeZone)}</time>
        ) : (
          <span className="text-slate-400">Never</span>
        )}
      </td>
    </tr>
  )
}

function Pagination({
  page,
  total,
  onPage,
}: {
  page: number
  total: number
  onPage: (page: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
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
