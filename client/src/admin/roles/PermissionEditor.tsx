import { useEffect, useId, useRef, useState } from 'react'

import type { PermissionGroup } from '../../api/types'
import { fieldClass, focusRing } from '../../components/styles'
import { plural } from '../../lib/format'
import { filterCatalog, groupLabel } from './catalog'

const NOT_HELD = "You don't have this permission, so you can't grant it."

/**
 * Checkboxes for the permission catalog, grouped by area, with a filter box and a
 * "select all" per group. Permissions you don't hold are disabled with the reason.
 */
export function PermissionEditor({
  groups,
  selected,
  onChange,
  canGrant,
  readOnly = false,
}: {
  groups: readonly PermissionGroup[]
  selected: ReadonlySet<string>
  onChange: (next: Set<string>) => void
  /** Whether the signed-in user may grant this key. */
  canGrant: (key: string) => boolean
  readOnly?: boolean
}) {
  const [filter, setFilter] = useState('')
  const filterId = useId()
  const notHeldId = useId()
  const visible = filterCatalog(groups, filter)
  const shown = visible.reduce((n, g) => n + g.permissions.length, 0)

  function toggle(key: string, on: boolean) {
    const next = new Set(selected)
    if (on) next.add(key)
    else next.delete(key)
    onChange(next)
  }

  function toggleGroup(keys: string[], on: boolean) {
    const next = new Set(selected)
    for (const key of keys) {
      if (on) next.add(key)
      else next.delete(key)
    }
    onChange(next)
  }

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <label htmlFor={filterId} className="block text-sm font-medium text-slate-700">
          Filter permissions
        </label>
        <input
          id={filterId}
          type="search"
          value={filter}
          placeholder="e.g. check in, guests, refund"
          onChange={(e) => {
            setFilter(e.target.value)
          }}
          className={`${fieldClass} max-w-sm`}
        />
        <p role="status" className="text-xs text-slate-500">
          {filter.trim() ? `${plural(shown, 'permission')} shown` : ''}
        </p>
      </div>
      {/* One shared explanation for every permission you can't grant. */}
      <p id={notHeldId} hidden>
        {NOT_HELD}
      </p>
      {visible.length === 0 ? (
        <p className="text-sm text-slate-600">No permissions match “{filter.trim()}”.</p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {visible.map((group) => (
            <GroupFieldset
              key={group.group}
              group={group}
              selected={selected}
              readOnly={readOnly}
              canGrant={canGrant}
              notHeldId={notHeldId}
              onToggle={toggle}
              onToggleGroup={toggleGroup}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function GroupFieldset({
  group,
  selected,
  readOnly,
  canGrant,
  notHeldId,
  onToggle,
  onToggleGroup,
}: {
  group: PermissionGroup
  selected: ReadonlySet<string>
  readOnly: boolean
  canGrant: (key: string) => boolean
  notHeldId: string
  onToggle: (key: string, on: boolean) => void
  onToggleGroup: (keys: string[], on: boolean) => void
}) {
  const label = groupLabel(group.group)
  // "Select all" covers the permissions you may change (and are shown).
  const changeable = group.permissions.map((p) => p.key).filter((key) => canGrant(key))
  const selectedCount = changeable.filter((key) => selected.has(key)).length
  const all = changeable.length > 0 && selectedCount === changeable.length
  const some = selectedCount > 0 && !all
  const allRef = useRef<HTMLInputElement>(null)
  const allId = useId()

  useEffect(() => {
    if (allRef.current) allRef.current.indeterminate = some
  }, [some])

  return (
    <fieldset className="rounded-lg border border-slate-200 p-4">
      <legend className="px-1 text-sm font-semibold text-slate-900">{label}</legend>
      {!readOnly && changeable.length > 0 && (
        <div className="mb-2 flex items-center gap-2 border-b border-slate-100 pb-2">
          <input
            ref={allRef}
            id={allId}
            type="checkbox"
            checked={all}
            aria-checked={some ? 'mixed' : all}
            onChange={() => {
              onToggleGroup(changeable, !all)
            }}
            className={`size-4 rounded border-slate-300 ${focusRing}`}
          />
          <label htmlFor={allId} className="text-sm text-slate-700">
            Select all {label.toLowerCase()} permissions
          </label>
        </div>
      )}
      <ul className="space-y-2">
        {group.permissions.map((permission) => (
          <PermissionCheckbox
            key={permission.key}
            permissionKey={permission.key}
            description={permission.description}
            checked={selected.has(permission.key)}
            grantable={canGrant(permission.key)}
            readOnly={readOnly}
            notHeldId={notHeldId}
            onToggle={onToggle}
          />
        ))}
      </ul>
    </fieldset>
  )
}

function PermissionCheckbox({
  permissionKey,
  description,
  checked,
  grantable,
  readOnly,
  notHeldId,
  onToggle,
}: {
  permissionKey: string
  description: string
  checked: boolean
  grantable: boolean
  readOnly: boolean
  notHeldId: string
  onToggle: (key: string, on: boolean) => void
}) {
  const id = useId()
  const blocked = !readOnly && !grantable
  return (
    <li className="flex items-start gap-2">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={readOnly || !grantable}
        aria-describedby={blocked ? notHeldId : undefined}
        onChange={(e) => {
          onToggle(permissionKey, e.target.checked)
        }}
        className={`mt-0.5 size-4 rounded border-slate-300 disabled:opacity-50 ${focusRing}`}
      />
      <label htmlFor={id} className="text-sm">
        <span className={blocked ? 'text-slate-500' : 'text-slate-900'}>{description}</span>
        <code className="ml-2 text-xs text-slate-500">{permissionKey}</code>
        {blocked && <span className="block text-xs text-slate-500">Not yours to grant</span>}
      </label>
    </li>
  )
}
