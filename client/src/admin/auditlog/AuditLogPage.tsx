import { ChevronDown, ChevronRight } from 'lucide-react'
import { Fragment, useEffect, useId, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import type { AuditEntry } from '../../api/types'
import { useAuthenticatedUser } from '../../auth/currentUser'
import { P } from '../../auth/permissions'
import { usePermissions } from '../../auth/usePermissions'
import { buttonSecondary, fieldClass, focusRing } from '../../components/styles'
import { formatDateTime, plural } from '../../lib/format'
import { ACTION_NAMES, ACTIONS, type ActionGroup, actionLabel, isActionName } from '../audit'
import { AuditDetails } from './AuditDetails'
import {
  type AuditFilters,
  filterProblem,
  hasFilters,
  NO_FILTERS,
  parseFilters,
  TARGET_LABELS,
  TARGET_TYPES,
  toSearch,
} from './params'
import { useAuditLog } from './queries'
import { useDocumentTitle } from '../../lib/useDocumentTitle'

const DEBOUNCE_MS = 300
const GROUPS: ActionGroup[] = ['Sign-in', 'Users', 'Roles', 'Room setup', 'Front desk']

/** /admin/audit: every data change and sign-in event, newest first. */
export function AuditLogPage() {
  useDocumentTitle('Audit log')
  const [search, setSearch] = useSearchParams()
  const filters = parseFilters(search)
  const problem = filterProblem(filters)
  const log = useAuditLog(filters, problem === null)

  function update(changes: Partial<AuditFilters>) {
    setSearch(toSearch({ ...filters, ...changes }), { replace: true })
  }

  return (
    <section aria-labelledby="audit-heading" className="space-y-4">
      <div>
        <h1 id="audit-heading" className="text-xl font-semibold text-slate-900">
          Audit log
        </h1>
        <p className="text-sm text-slate-600">
          Every change made by staff and every sign-in, newest first. Entries cannot be edited or
          deleted.
        </p>
      </div>
      <Filters filters={filters} problem={problem} onChange={update} />
      <div className="rounded-lg bg-white shadow-sm">
        <Results
          log={log}
          problem={problem}
          filtered={hasFilters(filters)}
          onClear={() => {
            setSearch(toSearch(NO_FILTERS), { replace: true })
          }}
        />
      </div>
    </section>
  )
}

/** A text input that writes to the URL after typing pauses (and at once on Enter). */
function DebouncedText({
  label,
  value,
  placeholder,
  maxLength,
  onCommit,
}: {
  label: string
  value: string
  placeholder?: string
  maxLength: number
  onCommit: (value: string) => void
}) {
  const id = useId()
  const [text, setText] = useState(value)
  const [lastValue, setLastValue] = useState(value)
  const commitRef = useRef(onCommit)
  useEffect(() => {
    commitRef.current = onCommit
  })
  if (value !== lastValue) {
    // Changed from outside (Clear filters, back button).
    setLastValue(value)
    setText(value)
  }
  useEffect(() => {
    if (text === value) return
    const timer = setTimeout(() => {
      commitRef.current(text)
    }, DEBOUNCE_MS)
    return () => {
      clearTimeout(timer)
    }
  }, [text, value])

  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-medium text-slate-700">
        {label}
      </label>
      <input
        id={id}
        type="text"
        value={text}
        maxLength={maxLength}
        placeholder={placeholder}
        autoCapitalize="none"
        spellCheck={false}
        onChange={(e) => {
          setText(e.target.value)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            onCommit(text)
          }
        }}
        className={fieldClass}
      />
    </div>
  )
}

function Filters({
  filters,
  problem,
  onChange,
}: {
  filters: AuditFilters
  problem: string | null
  onChange: (changes: Partial<AuditFilters>) => void
}) {
  const actionId = useId()
  const typeId = useId()
  const fromId = useId()
  const toId = useId()
  const problemId = useId()

  return (
    <form
      role="search"
      aria-label="Filter the audit log"
      onSubmit={(e) => {
        e.preventDefault()
      }}
      className="grid gap-3 rounded-lg bg-white p-4 shadow-sm sm:grid-cols-2 lg:grid-cols-3"
    >
      <DebouncedText
        label="Actor username"
        value={filters.actor}
        maxLength={50}
        placeholder="e.g. frontdesk"
        onCommit={(actor) => {
          onChange({ actor })
        }}
      />
      <div className="space-y-1">
        <label htmlFor={actionId} className="block text-sm font-medium text-slate-700">
          Action
        </label>
        <select
          id={actionId}
          value={filters.action ?? ''}
          onChange={(e) => {
            const value = e.target.value
            onChange({ action: isActionName(value) ? value : null })
          }}
          className={fieldClass}
        >
          <option value="">All actions</option>
          {GROUPS.map((group) => (
            <optgroup key={group} label={group}>
              {ACTION_NAMES.filter((name) => ACTIONS[name].group === group).map((name) => (
                <option key={name} value={name}>
                  {ACTIONS[name].label}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <label htmlFor={typeId} className="block text-sm font-medium text-slate-700">
            Target type
          </label>
          <select
            id={typeId}
            value={filters.targetType ?? ''}
            onChange={(e) => {
              const value = e.target.value
              onChange({ targetType: TARGET_TYPES.find((t) => t === value) ?? null })
            }}
            className={fieldClass}
          >
            <option value="">Any</option>
            {TARGET_TYPES.map((type) => (
              <option key={type} value={type}>
                {TARGET_LABELS[type]}
              </option>
            ))}
          </select>
        </div>
        <DebouncedText
          label="Target ID"
          value={filters.targetId}
          maxLength={64}
          onCommit={(targetId) => {
            onChange({ targetId })
          }}
        />
      </div>
      <div className="space-y-1">
        <label htmlFor={fromId} className="block text-sm font-medium text-slate-700">
          From
        </label>
        <input
          id={fromId}
          type="date"
          value={filters.from}
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? problemId : undefined}
          onChange={(e) => {
            onChange({ from: e.target.value })
          }}
          className={`${fieldClass} aria-invalid:border-red-600`}
        />
      </div>
      <div className="space-y-1">
        <label htmlFor={toId} className="block text-sm font-medium text-slate-700">
          To
        </label>
        <input
          id={toId}
          type="date"
          value={filters.to}
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? problemId : undefined}
          onChange={(e) => {
            onChange({ to: e.target.value })
          }}
          className={`${fieldClass} aria-invalid:border-red-600`}
        />
      </div>
      <p className="self-end text-xs text-slate-500 sm:col-span-2 lg:col-span-1">
        Dates are days in the hotel's time zone, both included.
      </p>
      {problem && (
        <p id={problemId} role="alert" className="text-sm text-red-700 sm:col-span-2 lg:col-span-3">
          {problem}
        </p>
      )}
    </form>
  )
}

function Results({
  log,
  problem,
  filtered,
  onClear,
}: {
  log: ReturnType<typeof useAuditLog>
  problem: string | null
  filtered: boolean
  onClear: () => void
}) {
  if (problem) {
    return <p className="p-6 text-slate-600">Fix the dates to see entries.</p>
  }
  if (log.isPending) {
    return (
      <p role="status" className="p-6 text-slate-600">
        Loading the audit log…
      </p>
    )
  }
  if (log.isError) {
    return (
      <div role="alert" className="space-y-3 p-6">
        <p className="text-slate-800">The audit log could not be loaded.</p>
        <button
          type="button"
          onClick={() => {
            void log.refetch()
          }}
          className={buttonSecondary}
        >
          Try again
        </button>
      </div>
    )
  }

  const entries = log.data.pages.flatMap((page) => page.items)
  return (
    <>
      <p role="status" className="border-b border-slate-200 px-4 py-3 text-sm text-slate-600">
        {entries.length === 0
          ? 'No entries found'
          : `Showing ${plural(entries.length, 'entry', 'entries')}${log.hasNextPage ? ' (older entries available)' : ''}`}
      </p>
      {entries.length === 0 ? (
        <div className="space-y-3 p-6 text-slate-700">
          <p>{filtered ? 'No entries match these filters.' : 'Nothing has been recorded yet.'}</p>
          {filtered && (
            <button type="button" onClick={onClear} className={buttonSecondary}>
              Clear filters
            </button>
          )}
        </div>
      ) : (
        <AuditTable entries={entries} />
      )}
      {log.hasNextPage && (
        <div className="border-t border-slate-200 p-4">
          <button
            type="button"
            disabled={log.isFetchingNextPage}
            onClick={() => {
              void log.fetchNextPage()
            }}
            className={buttonSecondary}
          >
            {log.isFetchingNextPage ? 'Loading…' : 'Load older entries'}
          </button>
        </div>
      )}
    </>
  )
}

function AuditTable({ entries }: { entries: AuditEntry[] }) {
  const { property } = useAuthenticatedUser()
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set())

  function toggle(id: string) {
    setOpen((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Audit log, newest first</caption>
        <thead className="bg-slate-50 text-slate-700">
          <tr>
            <th scope="col" className="w-10 px-2 py-2">
              <span className="sr-only">Details</span>
            </th>
            <th scope="col" className="px-4 py-2 font-semibold">
              Time
            </th>
            <th scope="col" className="px-4 py-2 font-semibold">
              Actor
            </th>
            <th scope="col" className="px-4 py-2 font-semibold">
              Action
            </th>
            <th scope="col" className="px-4 py-2 font-semibold">
              Target
            </th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => {
            const expanded = open.has(entry.id)
            const detailsId = `audit-details-${entry.id}`
            const time = formatDateTime(entry.occurred_at, property.time_zone)
            return (
              <Fragment key={entry.id}>
                <tr className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-2 py-2">
                    <button
                      type="button"
                      aria-expanded={expanded}
                      aria-controls={detailsId}
                      onClick={() => {
                        toggle(entry.id)
                      }}
                      className={`rounded p-1 text-slate-600 hover:bg-slate-100 ${focusRing}`}
                    >
                      {expanded ? (
                        <ChevronDown aria-hidden="true" className="size-4" />
                      ) : (
                        <ChevronRight aria-hidden="true" className="size-4" />
                      )}
                      <span className="sr-only">
                        Details: {actionLabel(entry.action)}, {time}
                      </span>
                    </button>
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap text-slate-700">
                    <time dateTime={entry.occurred_at}>{time}</time>
                  </td>
                  <td className="px-4 py-2">
                    <Actor entry={entry} />
                  </td>
                  <td className="px-4 py-2 text-slate-900">{actionLabel(entry.action)}</td>
                  <td className="px-4 py-2">
                    <Target entry={entry} />
                  </td>
                </tr>
                {expanded && (
                  <tr id={detailsId} className="bg-slate-50">
                    <td />
                    <td colSpan={4} className="px-4 pt-1 pb-4">
                      <AuditDetails entry={entry} />
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function Actor({ entry }: { entry: AuditEntry }) {
  const { actor } = entry
  if (!actor) return <span className="text-slate-500">System</span>
  if (actor.id === null) {
    return (
      <span className="text-slate-700">
        {actor.username} <span className="text-xs text-slate-500">(no such user)</span>
      </span>
    )
  }
  return (
    <span>
      <span className="block text-slate-900">{actor.full_name ?? actor.username}</span>
      <span className="block text-xs text-slate-500">{actor.username}</span>
    </span>
  )
}

function Target({ entry }: { entry: AuditEntry }) {
  const { can } = usePermissions()
  const { target_type: type, target_id: id, target_label: name } = entry
  if (!type || !id) return <span className="text-slate-400">—</span>
  const kind = TARGET_LABELS[type]
  const text = name ?? `${kind} ${id}`
  // Deleted roles have no page to go to.
  const link =
    type === 'user' && can(P.USER_VIEW)
      ? `/admin/users/${id}`
      : type === 'role' && can(P.ROLE_VIEW) && entry.action !== 'role.deleted'
        ? `/admin/roles/${id}`
        : null
  return (
    <span>
      {link ? (
        <Link to={link} className={`rounded-sm text-sky-800 hover:underline ${focusRing}`}>
          {text}
        </Link>
      ) : (
        <span className="text-slate-900">{text}</span>
      )}
      <span className="block text-xs text-slate-500">{kind}</span>
    </span>
  )
}
