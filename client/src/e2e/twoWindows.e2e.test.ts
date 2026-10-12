/**
 * Two "browser windows" against the real stack: each is a separate login with its own
 * query cache and the app's own realtime code (RealtimeConnection + cache patching),
 * talking to the backend through the Vite dev proxy. Window A checks a guest out over
 * REST; window B must show the change from the WebSocket alone, within a second.
 *
 * Requires: database, backend (seeded with `python -m app.scripts.seed --reset`) and
 * `npm run dev`. Run with `npm run test:e2e`.
 */
import { QueryClient } from '@tanstack/react-query'
import { afterAll, describe, expect, it } from 'vitest'

import type { DashboardRoom, DashboardRooms, DashboardSummary } from '../api/types'
import { dashboardKeys } from '../dashboard/queries'
import type { RealtimeConnection, SocketLike } from '../realtime/connection'
import { createCacheConnection } from '../realtime/useRealtime'

const BASE = 'http://localhost:5173'
const WS_URL = 'ws://localhost:5173/ws'

// Node's WebSocket accepts request headers (browsers send the cookie automatically).
type NodeWebSocketConstructor = new (
  url: string,
  init: { headers: Record<string, string> },
) => SocketLike
const NodeWebSocket = globalThis.WebSocket as unknown as NodeWebSocketConstructor

interface BrowserWindow {
  cookie: string
  queryClient: QueryClient
  connection: RealtimeConnection
}

const opened: BrowserWindow[] = []

async function login(username: string): Promise<string> {
  const response = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password: `${username}-dev-pass` }),
  })
  expect(response.status).toBe(200)
  const cookie = response.headers.getSetCookie()[0]?.split(';')[0]
  if (!cookie) throw new Error('no session cookie')
  return cookie
}

async function get<T>(cookie: string, path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { headers: { cookie } })
  expect(response.status).toBe(200)
  return (await response.json()) as T
}

/** Like the dashboard page: open the socket, wait for "live", load the snapshot. */
async function openWindow(username: string): Promise<BrowserWindow> {
  const cookie = await login(username)
  const queryClient = new QueryClient()
  const connection = createCacheConnection(queryClient, WS_URL, {
    createSocket: (url) => new NodeWebSocket(url, { headers: { cookie, origin: BASE } }),
  })
  const live = new Promise<void>((resolve) => {
    connection.subscribe((status) => {
      if (status === 'live') resolve()
    })
  })
  connection.start()
  await live
  queryClient.setQueryData(
    dashboardKeys.rooms,
    await get<DashboardRooms>(cookie, '/api/dashboard/rooms'),
  )
  queryClient.setQueryData(
    dashboardKeys.summary,
    await get<DashboardSummary>(cookie, '/api/dashboard/summary'),
  )
  const window: BrowserWindow = { cookie, queryClient, connection }
  opened.push(window)
  return window
}

function cachedRoom(window: BrowserWindow, roomId: number): DashboardRoom | undefined {
  return window.queryClient
    .getQueryData<DashboardRooms>(dashboardKeys.rooms)
    ?.rooms.find((r) => r.room_id === roomId)
}

async function waitFor(condition: () => boolean, timeoutMs: number): Promise<number> {
  const start = performance.now()
  while (performance.now() - start < timeoutMs) {
    if (condition()) return performance.now() - start
    await new Promise((resolve) => setTimeout(resolve, 5))
  }
  throw new Error(`condition not met within ${timeoutMs} ms`)
}

afterAll(() => {
  for (const window of opened) window.connection.stop()
})

describe('two windows', () => {
  it('a check-out in window A appears in window B within a second', async () => {
    const windowA = await openWindow('frontdesk')
    const windowB = await openWindow('manager')

    const target = windowA.queryClient
      .getQueryData<DashboardRooms>(dashboardKeys.rooms)
      ?.rooms.find((r) => r.occupancy_status === 'departing_today' && r.reservation_id !== null)
    if (!target?.reservation_id) throw new Error('seed data has no departing guest; reseed')
    const summaryBefore = windowB.queryClient.getQueryData<DashboardSummary>(dashboardKeys.summary)

    const start = performance.now()
    const response = await fetch(`${BASE}/api/reservations/${target.reservation_id}/check-out`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', cookie: windowA.cookie },
      body: JSON.stringify({ room_version: target.version }),
    })
    expect(response.status).toBe(200)

    await waitFor(() => cachedRoom(windowB, target.room_id)?.version === target.version + 1, 1000)
    const elapsed = performance.now() - start

    const roomInB = cachedRoom(windowB, target.room_id)
    expect(roomInB?.housekeeping_status).toBe('dirty')
    expect(roomInB?.guest_name).not.toBe(target.guest_name)
    const summaryAfter = windowB.queryClient.getQueryData<DashboardSummary>(dashboardKeys.summary)
    expect(summaryAfter?.departures_checked_out).toBe(
      (summaryBefore?.departures_checked_out ?? 0) + 1,
    )
    expect(elapsed).toBeLessThan(1000)
    console.log(
      `Room ${target.number}: window B updated ${elapsed.toFixed(0)} ms after window A's check-out request started`,
    )
  })
})
