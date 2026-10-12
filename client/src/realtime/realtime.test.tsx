import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { BUSINESS_DATE, fixtureRoom, HOTEL, ROOMS, SUMMARY } from '../test/fixtures'
import { FakeWebSocket } from '../test/fakeWebSocket'
import { FRONT_DESK_USER, ROOM_ATTENDANT_USER, mockApi, renderApp } from '../test/renderApp'

function liveDashboard(user: object = FRONT_DESK_USER) {
  const requests = { rooms: 0, summary: 0 }
  const fetchMock = mockApi({
    'GET /api/auth/me': () => [200, user],
    'GET /api/hotel': () => [200, HOTEL],
    'GET /api/dashboard/rooms': () => {
      requests.rooms += 1
      return [200, { business_date: BUSINESS_DATE, rooms: ROOMS }]
    },
    'GET /api/dashboard/summary': () => {
      requests.summary += 1
      return [200, SUMMARY]
    },
  })
  renderApp('/')
  return { requests, fetchMock }
}

async function connected() {
  await screen.findByRole('button', { name: /^Room 201,/ })
  const socket = FakeWebSocket.latest()
  socket.hello(0)
  return socket
}

describe('live dashboard', () => {
  it('opens the socket on the page origin', async () => {
    liveDashboard()
    await screen.findByRole('button', { name: /^Room 201,/ })

    expect(FakeWebSocket.latest().url).toBe(`ws://${window.location.host}/ws`)
  })

  it('patches a room from room_status_changed without refetching the room list', async () => {
    const { requests } = liveDashboard()
    const socket = await connected()
    // The hello triggers one snapshot resync; wait for it, then count.
    await waitFor(() => {
      expect(requests.rooms).toBe(2)
    })

    socket.receive({
      type: 'room_status_changed',
      seq: 1,
      payload: {
        business_date: BUSINESS_DATE,
        room: {
          ...fixtureRoom(4),
          occupancy_status: 'vacant',
          guest_name: null,
          reservation_id: null,
          version: 2,
        },
      },
    })

    expect(await screen.findByRole('button', { name: /^Room 201, vacant/ })).toBeInTheDocument()
    expect(requests.rooms).toBe(2)
  })

  it('updates the summary from summary_changed', async () => {
    const { requests } = liveDashboard()
    const socket = await connected()
    await waitFor(() => {
      expect(requests.summary).toBe(2)
    })

    socket.receive({
      type: 'summary_changed',
      seq: 1,
      payload: { summary: { ...SUMMARY, dirty: 7 } },
    })

    const summary = screen.getByRole('list', { name: "Today's summary" })
    await waitFor(() => {
      expect(within(summary).getByRole('button', { name: /^Dirty/ })).toHaveTextContent('7')
    })
    expect(requests.summary).toBe(2)
  })

  it('shows the connection state in the header', async () => {
    liveDashboard()
    const header = await screen.findByRole('banner')
    expect(within(header).getByRole('status', { name: 'Live updates: Connecting' })).toBeVisible()

    const socket = await connected()
    expect(
      await within(header).findByRole('status', { name: 'Live updates: Live' }),
    ).toBeInTheDocument()

    socket.serverClose(1006)
    expect(
      await within(header).findByRole('status', { name: 'Live updates: Reconnecting' }),
    ).toBeInTheDocument()
  })

  it('refetches the full snapshot after reconnecting', async () => {
    const { requests } = liveDashboard()
    const socket = await connected()
    await waitFor(() => {
      expect(requests.rooms).toBe(2)
    })

    socket.serverClose(1006)
    // Backoff: about one second for the first retry.
    await waitFor(
      () => {
        expect(FakeWebSocket.instances.length).toBeGreaterThan(1)
      },
      { timeout: 2000 },
    )
    FakeWebSocket.latest().hello(0)

    await waitFor(() => {
      expect(requests.rooms).toBe(3)
    })
    expect(requests.summary).toBe(3)
    expect(await screen.findByRole('status', { name: 'Live updates: Live' })).toBeInTheDocument()
  })

  it('refetches the user and the dashboard when permissions change', async () => {
    let meCalls = 0
    const requests = { rooms: 0 }
    mockApi({
      'GET /api/auth/me': () => {
        meCalls += 1
        // As if an admin edited the role: the fresh /me reflects it.
        return [
          200,
          meCalls === 1 ? FRONT_DESK_USER : { ...FRONT_DESK_USER, full_name: 'Fran Lead' },
        ]
      },
      'GET /api/hotel': () => [200, HOTEL],
      'GET /api/dashboard/rooms': () => {
        requests.rooms += 1
        return [200, { business_date: BUSINESS_DATE, rooms: ROOMS }]
      },
      'GET /api/dashboard/summary': () => [200, SUMMARY],
    })
    renderApp('/')
    const socket = await connected()
    await waitFor(() => {
      expect(requests.rooms).toBe(2)
    })

    socket.receive({ type: 'permissions_changed', seq: 0, payload: {} })

    expect(await within(screen.getByRole('banner')).findByText('Fran Lead')).toBeInTheDocument()
    await waitFor(() => {
      expect(requests.rooms).toBe(3)
    })
  })

  it('does not open a socket for roles without dashboard access', async () => {
    liveDashboard(ROOM_ATTENDANT_USER)
    await screen.findByRole('banner')

    expect(FakeWebSocket.instances).toHaveLength(0)
    expect(screen.queryByRole('status', { name: /Live updates/ })).not.toBeInTheDocument()
  })

  it('closes the socket when leaving the signed-in area', async () => {
    mockApi({
      'GET /api/auth/me': () => [200, FRONT_DESK_USER],
      'GET /api/hotel': () => [200, HOTEL],
      'GET /api/dashboard/rooms': () => [200, { business_date: BUSINESS_DATE, rooms: ROOMS }],
      'GET /api/dashboard/summary': () => [200, SUMMARY],
      'POST /api/auth/logout': () => [204],
    })
    renderApp('/')
    const socket = await connected()

    screen.getByRole('button', { name: 'Sign out' }).click()

    await screen.findByRole('heading', { name: 'Sign in to Front Desk' })
    expect(socket.closed?.code).toBe(1000)
  })
})
