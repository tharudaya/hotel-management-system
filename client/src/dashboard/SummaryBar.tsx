import type { DashboardSummary } from '../api/types'
import { focusRing } from '../components/styles'
import type { QuickView } from './filters'

interface SummaryBarProps {
  summary: DashboardSummary
  activeView: QuickView | null
  onSelectView: (view: QuickView | null) => void
}

interface Item {
  view: QuickView
  label: string
  value: string
  detail?: string
}

function items(s: DashboardSummary): Item[] {
  return [
    {
      view: 'arrivals',
      label: 'Arrivals',
      value: `${s.arrivals_checked_in}/${s.arrivals_today}`,
      detail: 'checked in',
    },
    {
      view: 'departures',
      label: 'Departures',
      value: `${s.departures_checked_out}/${s.departures_today}`,
      detail: 'checked out',
    },
    {
      view: 'in_house',
      label: 'In house',
      value: String(s.in_house),
      detail: `${s.in_house_guests} guests`,
    },
    { view: 'available', label: 'Available tonight', value: String(s.available_tonight) },
    { view: 'dirty', label: 'Dirty', value: String(s.dirty) },
    { view: 'out_of_order', label: 'Out of order', value: String(s.out_of_order) },
  ]
}

/** Daily counts; each count toggles a matching filter on the room grid. */
export function SummaryBar({ summary, activeView, onSelectView }: SummaryBarProps) {
  return (
    <ul
      aria-label="Today's summary"
      className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7"
    >
      {items(summary).map((item) => {
        const active = item.view === activeView
        return (
          <li key={item.view}>
            <button
              type="button"
              aria-pressed={active}
              onClick={() => {
                onSelectView(active ? null : item.view)
              }}
              className={`flex h-full w-full flex-col rounded-lg border px-3 py-2 text-left ${active ? 'border-sky-700 bg-sky-50' : 'border-slate-200 bg-white hover:bg-slate-50'} ${focusRing}`}
            >
              <span className="text-xs font-medium text-slate-600">{item.label}</span>
              <span className="text-2xl font-semibold text-slate-900">{item.value}</span>
              {item.detail && <span className="text-xs text-slate-500">{item.detail}</span>}
            </button>
          </li>
        )
      })}
      <li className="flex flex-col rounded-lg border border-slate-200 bg-white px-3 py-2">
        <span className="text-xs font-medium text-slate-600">Occupancy</span>
        <span className="text-2xl font-semibold text-slate-900">{summary.occupancy_percent}%</span>
        <span className="text-xs text-slate-500">tonight</span>
      </li>
    </ul>
  )
}
