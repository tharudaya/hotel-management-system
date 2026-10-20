/**
 * Two dashboards in two browser windows: a check-out in one reaches the other over the
 * WebSocket alone (no reload) within a second.
 */
import { expect, test } from '@playwright/test'

import { api, USERS, window } from './support.ts'

interface Tile {
  room_id: number
  number: string
  guest_name: string | null
  occupancy_status: string
  reservation_id: number | null
}

test('a check-out in one window appears in the other within a second', async ({ browser }) => {
  // A guest due out today, from the seed.
  const desk = await api(USERS.receptionist)
  const tiles = ((await (await desk.get('/api/dashboard/rooms')).json()) as { rooms: Tile[] }).rooms
  await desk.dispose()
  const target = tiles.find(
    (t) => t.occupancy_status === 'departing_today' && t.reservation_id !== null,
  )
  if (!target?.guest_name) throw new Error('the seed has no guest due out today')
  const guest = target.guest_name

  const [a, b] = await Promise.all([
    window(browser, USERS.receptionist),
    window(browser, USERS.frontOfficeManager),
  ])
  const tileIn = (page: typeof a) => page.locator(`[data-room-id="${target.room_id}"]`)
  await expect(tileIn(b)).toHaveAttribute('aria-label', new RegExp(`guest ${guest}`))
  await expect(b.getByText('Live')).toBeVisible()

  // Window A checks the guest out from the room's panel.
  await tileIn(a).click()
  const panel = a.getByRole('complementary', { name: `Room ${target.number}` })
  const checkedOut = a.waitForResponse(
    (r) => r.url().endsWith('/check-out') && r.request().method() === 'POST',
  )
  await panel.getByRole('button', { name: /^Check out/ }).click()
  expect((await checkedOut).ok()).toBe(true)

  // Window B shows it without reloading.
  const started = Date.now()
  await expect(tileIn(b)).not.toHaveAttribute('aria-label', new RegExp(`guest ${guest}`), {
    timeout: 1_000,
  })
  console.log(
    `Room ${target.number}: window B updated ${Date.now() - started} ms after the check-out`,
  )

  await a.context().close()
  await b.context().close()
})
