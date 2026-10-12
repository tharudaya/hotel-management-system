import { Search } from 'lucide-react'
import { useId } from 'react'

import type { DashboardRoom } from '../api/types'
import { buttonSecondary, fieldClass } from '../components/styles'
import { hasActiveFilters, NO_FILTERS, type RoomFilters } from './filters'
import { HOUSEKEEPING, HOUSEKEEPING_ORDER, OCCUPANCY, OCCUPANCY_ORDER } from './status'

interface FilterBarProps {
  rooms: DashboardRoom[]
  filters: RoomFilters
  onChange: (filters: RoomFilters) => void
}

export function FilterBar({ rooms, filters, onChange }: FilterBarProps) {
  const id = useId()
  const floors = [...new Set(rooms.map((r) => r.floor))].sort((a, b) => a - b)
  const roomTypes = [...new Map(rooms.map((r) => [r.room_type, r.room_type_name])).entries()].sort()

  function update(patch: Partial<RoomFilters>) {
    onChange({ ...filters, ...patch })
  }

  return (
    <div
      role="search"
      aria-label="Filter rooms"
      className="grid grid-cols-2 items-end gap-3 sm:grid-cols-3 lg:grid-cols-6"
    >
      <div className="col-span-2 sm:col-span-3 lg:col-span-2">
        <label htmlFor={`${id}-search`} className="mb-1 block text-xs font-medium text-slate-600">
          Room or guest
        </label>
        <div className="relative">
          <Search
            aria-hidden
            className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-slate-400"
          />
          <input
            id={`${id}-search`}
            type="search"
            placeholder="e.g. 304 or Lovelace"
            value={filters.search}
            onChange={(e) => {
              update({ search: e.target.value })
            }}
            className={`${fieldClass} pl-8`}
          />
        </div>
      </div>

      <Select
        id={`${id}-floor`}
        label="Floor"
        value={filters.floor === null ? '' : String(filters.floor)}
        onChange={(value) => {
          update({ floor: value === '' ? null : Number(value) })
        }}
        options={floors.map((floor) => [String(floor), `Floor ${floor}`])}
      />
      <Select
        id={`${id}-type`}
        label="Room type"
        value={filters.roomType ?? ''}
        onChange={(value) => {
          update({ roomType: value || null })
        }}
        options={roomTypes.map(([code, name]) => [code, name])}
      />
      <Select
        id={`${id}-occupancy`}
        label="Occupancy"
        value={filters.occupancy ?? ''}
        onChange={(value) => {
          update({ occupancy: OCCUPANCY_ORDER.find((s) => s === value) ?? null })
        }}
        options={OCCUPANCY_ORDER.map((s) => [s, OCCUPANCY[s].label])}
      />
      <Select
        id={`${id}-housekeeping`}
        label="Housekeeping"
        value={filters.housekeeping ?? ''}
        onChange={(value) => {
          update({ housekeeping: HOUSEKEEPING_ORDER.find((s) => s === value) ?? null })
        }}
        options={HOUSEKEEPING_ORDER.map((s) => [s, HOUSEKEEPING[s].label])}
      />

      {hasActiveFilters(filters) && (
        <button
          type="button"
          onClick={() => {
            onChange(NO_FILTERS)
          }}
          className={`${buttonSecondary} col-span-2 sm:col-span-1`}
        >
          Clear filters
        </button>
      )}
    </div>
  )
}

interface SelectProps {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  options: [value: string, label: string][]
}

function Select({ id, label, value, onChange, options }: SelectProps) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-slate-600">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => {
          onChange(e.target.value)
        }}
        className={fieldClass}
      >
        <option value="">All</option>
        {options.map(([optionValue, optionLabel]) => (
          <option key={optionValue} value={optionValue}>
            {optionLabel}
          </option>
        ))}
      </select>
    </div>
  )
}
