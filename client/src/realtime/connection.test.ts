import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fixtureRoom, SUMMARY } from '../test/fixtures'
import { FakeWebSocket } from '../test/fakeWebSocket'
import { type ConnectionStatus, RealtimeConnection } from './connection'
import type { RealtimeEvent } from './protocol'

function roomEvent(seq: number, roomId = 1, version = 2) {
  return {
    type: 'room_status_changed',
    seq,
    payload: { business_date: '2026-10-12', room: { ...fixtureRoom(roomId), version } },
  }
}

function summaryEvent(seq: number) {
  return { type: 'summary_changed', seq, payload: { summary: SUMMARY } }
}

function setup(overrides: { isOnline?: () => boolean } = {}) {
  const onEvents = vi.fn<(events: RealtimeEvent[]) => void>()
  const onResync = vi.fn()
  const onUnauthorized = vi.fn()
  const statuses: ConnectionStatus[] = []
  const connection = new RealtimeConnection({
    url: 'ws://test/ws',
    onEvents,
    onResync,
    onUnauthorized,
    createSocket: (url) => new FakeWebSocket(url),
    random: () => 0.5, // no jitter
    ...overrides,
  })
  connection.subscribe((s) => statuses.push(s))
  connection.start()
  return { connection, onEvents, onResync, onUnauthorized, statuses }
}

/** Sequence numbers of every event delivered so far, batch by batch. */
function batches(onEvents: ReturnType<typeof setup>['onEvents']): number[][] {
  return onEvents.mock.calls.map(([events]) => events.map((e) => e.seq))
}

beforeEach(() => {
  vi.useFakeTimers()
})
afterEach(() => {
  vi.useRealTimers()
})

describe('RealtimeConnection', () => {
  it('goes live on hello and resyncs the snapshot', () => {
    const { connection, onResync, statuses } = setup()

    expect(connection.getStatus()).toBe('connecting')
    FakeWebSocket.latest().hello(7)

    expect(statuses).toEqual(['live'])
    expect(onResync).toHaveBeenCalledOnce()
  })

  it('batches a burst of events into one delivery', () => {
    const { onEvents } = setup()
    const socket = FakeWebSocket.latest()
    socket.hello(0)

    socket.receive(roomEvent(1, 1))
    socket.receive(roomEvent(2, 2))
    socket.receive(summaryEvent(3))
    expect(onEvents).not.toHaveBeenCalled()

    vi.advanceTimersByTime(50)

    expect(batches(onEvents)).toEqual([[1, 2, 3]])
  })

  it('ignores events with a sequence number at or below the last one', () => {
    const { onEvents, onResync } = setup()
    const socket = FakeWebSocket.latest()
    socket.hello(5)

    socket.receive(roomEvent(4))
    socket.receive(roomEvent(5))
    socket.receive(roomEvent(6))
    socket.receive(roomEvent(6))
    vi.advanceTimersByTime(50)

    expect(batches(onEvents)).toEqual([[6]])
    expect(onResync).toHaveBeenCalledOnce() // only the hello
  })

  it('resyncs when a sequence gap shows events were missed', () => {
    const { onEvents, onResync } = setup()
    const socket = FakeWebSocket.latest()
    socket.hello(1)
    onResync.mockClear()

    socket.receive(roomEvent(2))
    socket.receive(roomEvent(5))
    vi.advanceTimersByTime(50)

    expect(batches(onEvents)).toEqual([[2, 5]])
    expect(onResync).toHaveBeenCalledOnce()
  })

  it('answers pings, and resyncs if the ping says events were missed', () => {
    const { onResync } = setup()
    const socket = FakeWebSocket.latest()
    socket.hello(3)
    onResync.mockClear()

    socket.receive({ type: 'ping', seq: 3 })
    vi.advanceTimersByTime(50)
    expect(socket.sent).toEqual([{ type: 'pong' }])
    expect(onResync).not.toHaveBeenCalled()

    socket.receive({ type: 'ping', seq: 4 })
    vi.advanceTimersByTime(50)
    expect(onResync).toHaveBeenCalledOnce()
  })

  it('takes the new baseline from hello after a server restart', () => {
    const { onEvents } = setup()
    FakeWebSocket.latest().hello(40)
    FakeWebSocket.latest().serverClose(1006)
    vi.advanceTimersByTime(1000)

    // Restarted server: its counter starts again from 0.
    const socket = FakeWebSocket.latest()
    socket.hello(0)
    socket.receive(roomEvent(1))
    vi.advanceTimersByTime(50)

    expect(batches(onEvents)).toEqual([[1]])
  })

  it('ignores malformed messages', () => {
    const { onEvents } = setup()
    const socket = FakeWebSocket.latest()
    socket.hello(0)

    socket.onmessage?.(new MessageEvent('message', { data: 'not json' }))
    socket.receive({ type: 'room_status_changed', seq: 1, payload: { room: 'nope' } })
    socket.receive({ type: 'mystery', seq: 2 })
    vi.advanceTimersByTime(50)

    expect(onEvents).not.toHaveBeenCalled()
  })

  it('reconnects with exponential backoff and resets it after a successful hello', () => {
    const { statuses } = setup()
    FakeWebSocket.latest().hello(0)

    const reconnectAfter = (ms: number) => {
      const before = FakeWebSocket.instances.length
      FakeWebSocket.latest().serverClose(1006)
      vi.advanceTimersByTime(ms - 1)
      expect(FakeWebSocket.instances).toHaveLength(before)
      vi.advanceTimersByTime(1)
      expect(FakeWebSocket.instances).toHaveLength(before + 1)
    }

    reconnectAfter(1000)
    reconnectAfter(2000)
    reconnectAfter(4000)
    expect(statuses.at(-1)).toBe('reconnecting')

    FakeWebSocket.latest().hello(0)
    expect(statuses.at(-1)).toBe('live')
    reconnectAfter(1000) // back to the first step
  })

  it('caps the backoff and reports offline after repeated failures', () => {
    const { statuses } = setup()
    for (let attempt = 0; attempt < 10; attempt++) {
      FakeWebSocket.latest().serverClose(1006)
      vi.advanceTimersByTime(30_000)
    }

    expect(statuses.at(-1)).toBe('offline')
    // Still retrying, at most every 30 s.
    const before = FakeWebSocket.instances.length
    FakeWebSocket.latest().serverClose(1006)
    vi.advanceTimersByTime(30_000)
    expect(FakeWebSocket.instances).toHaveLength(before + 1)
  })

  it('reconnects immediately when the browser comes back online', () => {
    let online = false
    const { statuses } = setup({ isOnline: () => online })
    FakeWebSocket.latest().serverClose(1006)
    expect(statuses.at(-1)).toBe('offline')
    const before = FakeWebSocket.instances.length

    online = true
    window.dispatchEvent(new Event('online'))

    expect(FakeWebSocket.instances).toHaveLength(before + 1)
  })

  it('stops and reports the lost session on 4401', () => {
    const { onUnauthorized, statuses } = setup()
    FakeWebSocket.latest().hello(0)
    const before = FakeWebSocket.instances.length

    FakeWebSocket.latest().serverClose(4401, 'Logged out')
    vi.advanceTimersByTime(60_000)

    expect(onUnauthorized).toHaveBeenCalledOnce()
    expect(statuses.at(-1)).toBe('offline')
    expect(FakeWebSocket.instances).toHaveLength(before)
  })

  it('does not retry when forbidden (4403)', () => {
    const { onUnauthorized } = setup()
    const before = FakeWebSocket.instances.length

    FakeWebSocket.latest().serverClose(4403)
    vi.advanceTimersByTime(60_000)

    expect(onUnauthorized).not.toHaveBeenCalled()
    expect(FakeWebSocket.instances).toHaveLength(before)
  })

  it('delivers events received just before the connection dropped', () => {
    const { onEvents } = setup()
    const socket = FakeWebSocket.latest()
    socket.hello(0)

    socket.receive(roomEvent(1))
    socket.serverClose(1006)

    expect(batches(onEvents)).toEqual([[1]])
  })

  it('stop() closes the socket and cancels any reconnect', () => {
    const { connection } = setup()
    const socket = FakeWebSocket.latest()
    socket.serverClose(1006)
    const before = FakeWebSocket.instances.length

    connection.stop()
    vi.advanceTimersByTime(60_000)

    expect(FakeWebSocket.instances).toHaveLength(before)
  })
})
