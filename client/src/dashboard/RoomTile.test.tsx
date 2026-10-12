import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { DashboardRoom, OccupancyStatus } from '../api/types'
import { makeRoom } from '../test/fixtures'
import { RoomTile } from './RoomTile'

function renderTile(room: DashboardRoom, onSelect = vi.fn()) {
  render(<RoomTile room={room} selected={false} onSelect={onSelect} />)
  return { tile: screen.getByRole('button'), onSelect }
}

const CASES: {
  status: OccupancyStatus
  room: Partial<DashboardRoom>
  visibleLabel: string
  colorClass: string
  accessibleName: string
}[] = [
  {
    status: 'vacant',
    room: { housekeeping_status: 'inspected' },
    visibleLabel: 'Vacant',
    colorClass: 'bg-emerald-50',
    accessibleName: 'Room 304, vacant, inspected',
  },
  {
    status: 'occupied',
    room: { housekeeping_status: 'dirty', guest_name: 'Ada Lovelace' },
    visibleLabel: 'Occupied',
    colorClass: 'bg-sky-100',
    accessibleName: 'Room 304, occupied, dirty, guest Ada Lovelace',
  },
  {
    status: 'arriving_today',
    room: { housekeeping_status: 'clean', guest_name: 'Grace Hopper', has_arrival_today: true },
    visibleLabel: 'Arriving',
    colorClass: 'bg-amber-50',
    accessibleName: 'Room 304, arriving today, clean, guest Grace Hopper',
  },
  {
    status: 'departing_today',
    room: { housekeeping_status: 'dirty', guest_name: 'Alan Turing' },
    visibleLabel: 'Departing',
    colorClass: 'bg-violet-100',
    accessibleName: 'Room 304, occupied, departing today, dirty, guest Alan Turing',
  },
  {
    status: 'out_of_order',
    room: { housekeeping_status: 'out_of_order' },
    visibleLabel: 'Out of order',
    colorClass: 'bg-slate-200',
    accessibleName: 'Room 304, out of order',
  },
]

describe('RoomTile', () => {
  it.each(CASES)(
    'renders $status with color, icon, text label and accessible name',
    ({ status, room, visibleLabel, colorClass, accessibleName }) => {
      const { tile } = renderTile(makeRoom({ number: '304', occupancy_status: status, ...room }))

      expect(tile).toHaveAccessibleName(accessibleName)
      expect(tile).toHaveClass(colorClass)
      // Color is never the only signal: a text label and an icon accompany it.
      const label = screen.getByText(visibleLabel, { exact: false, selector: 'span' })
      expect(label).toBeVisible()
      expect(label.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    },
  )

  it('shows room number, type, guest, housekeeping status and VIP badge', () => {
    const { tile } = renderTile(
      makeRoom({
        number: '304',
        room_type: 'DLX',
        occupancy_status: 'occupied',
        housekeeping_status: 'in_progress',
        guest_name: 'Ada Lovelace',
        is_vip: true,
        has_notes: true,
      }),
    )

    for (const text of ['304', 'DLX', 'Ada Lovelace', 'Cleaning', 'VIP']) {
      expect(tile).toHaveTextContent(text)
    }
    expect(tile).toHaveAccessibleName(
      'Room 304, occupied, cleaning in progress, guest Ada Lovelace, VIP, has notes',
    )
  })

  it('flags a turnover room (departing guest plus an arrival today)', () => {
    const { tile } = renderTile(
      makeRoom({
        number: '201',
        occupancy_status: 'departing_today',
        housekeeping_status: 'dirty',
        guest_name: 'Alan Turing',
        has_arrival_today: true,
      }),
    )

    expect(tile).toHaveTextContent('Departing · arrival')
    expect(tile).toHaveAccessibleName(
      'Room 201, occupied, departing today, arrival due today, dirty, guest Alan Turing',
    )
  })

  it('is keyboard operable', async () => {
    const { tile, onSelect } = renderTile(makeRoom({ room_id: 7 }))
    const user = userEvent.setup()

    await user.tab()
    expect(tile).toHaveFocus()
    await user.keyboard('{Enter}')
    await user.keyboard(' ')

    expect(onSelect).toHaveBeenCalledTimes(2)
    expect(onSelect).toHaveBeenCalledWith(7)
  })
})
