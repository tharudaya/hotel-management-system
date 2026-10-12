/**
 * WebSocket connection to the realtime hub, independent of React.
 *
 * - Answers the server's pings.
 * - Tracks sequence numbers per connection: the `hello` on each (re)connect sets the
 *   baseline (the server's counter restarts with the server), events at or below the
 *   last seen seq are ignored, and a jump means events were missed, so a resync
 *   (full snapshot refetch) is requested.
 * - Collects events for `batchMs` and delivers them together, so a burst causes one
 *   cache update instead of one per event.
 * - Reconnects with exponential backoff and jitter; immediately when the browser comes
 *   back online. Never reconnects after 4401 (session ended) or 4403 (forbidden).
 */
import {
  CLOSE_FORBIDDEN,
  CLOSE_UNAUTHENTICATED,
  parseServerMessage,
  type RealtimeEvent,
  type ServerMessage,
} from './protocol'

export type ConnectionStatus = 'connecting' | 'live' | 'reconnecting' | 'offline'

/** The subset of the browser WebSocket API used here (lets tests and Node supply their own). */
export interface SocketLike {
  send(data: string): void
  close(code?: number, reason?: string): void
  onopen: ((event: Event) => void) | null
  onmessage: ((event: MessageEvent) => void) | null
  onclose: ((event: CloseEvent) => void) | null
  onerror: ((event: Event) => void) | null
}

export interface RealtimeConnectionOptions {
  url: string
  /** Deliver a batch of new events, in sequence order. */
  onEvents: (events: RealtimeEvent[]) => void
  /** The client may have missed events: refetch the full snapshot. */
  onResync: () => void
  /** The server ended the session (logout, expiry): stop and go to login. */
  onUnauthorized: () => void
  createSocket?: (url: string) => SocketLike
  batchMs?: number
  initialBackoffMs?: number
  maxBackoffMs?: number
  /** After this many failed attempts in a row, report "offline" (retries continue). */
  offlineAfterAttempts?: number
  /** Injected for deterministic tests. */
  random?: () => number
  isOnline?: () => boolean
}

type Listener = (status: ConnectionStatus) => void

export class RealtimeConnection {
  private readonly options: Required<RealtimeConnectionOptions>
  private socket: SocketLike | null = null
  private status: ConnectionStatus = 'connecting'
  private readonly listeners = new Set<Listener>()
  private stopped = true
  private everConnected = false
  private failedAttempts = 0
  private lastSeq = 0
  private pending: RealtimeEvent[] = []
  private needsResync = false
  private flushTimer: ReturnType<typeof setTimeout> | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null

  constructor(options: RealtimeConnectionOptions) {
    this.options = {
      createSocket: (url) => new WebSocket(url),
      batchMs: 50,
      initialBackoffMs: 1000,
      maxBackoffMs: 30_000,
      offlineAfterAttempts: 5,
      random: Math.random,
      isOnline: () => (typeof navigator === 'undefined' ? true : navigator.onLine),
      ...options,
    }
  }

  getStatus = (): ConnectionStatus => this.status

  /** For useSyncExternalStore: returns an unsubscribe function. */
  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  start(): void {
    if (!this.stopped) return
    this.stopped = false
    if (typeof window !== 'undefined') window.addEventListener('online', this.handleOnline)
    this.connect()
  }

  stop(): void {
    this.stopped = true
    if (typeof window !== 'undefined') window.removeEventListener('online', this.handleOnline)
    this.clearTimers()
    this.pending = []
    const socket = this.socket
    this.socket = null
    if (socket) {
      this.detach(socket)
      socket.close(1000, 'Client closed')
    }
  }

  private connect(): void {
    this.reconnectTimer = null
    this.setStatus(
      this.everConnected || this.failedAttempts > 0 ? this.retryStatus() : 'connecting',
    )
    const socket = this.options.createSocket(this.options.url)
    this.socket = socket
    socket.onmessage = (event) => {
      if (typeof event.data === 'string') this.handleMessage(socket, event.data)
    }
    socket.onclose = (event) => {
      this.handleClose(socket, event.code)
    }
    // A failed connection also fires close, which drives the retry.
    socket.onerror = () => undefined
  }

  private handleMessage(socket: SocketLike, text: string): void {
    if (socket !== this.socket) return
    const message = parseServerMessage(text)
    if (message) this.handle(socket, message)
  }

  private handle(socket: SocketLike, message: ServerMessage): void {
    switch (message.type) {
      case 'hello':
        // New connection: the server's seq is the baseline. Whatever happened while we
        // were away is unknown, so always resync the snapshot.
        this.lastSeq = message.seq
        this.pending = []
        this.failedAttempts = 0
        this.everConnected = true
        this.setStatus('live')
        this.options.onResync()
        return
      case 'ping':
        socket.send(JSON.stringify({ type: 'pong' }))
        if (message.seq > this.lastSeq) {
          // The server has broadcast events we never received.
          this.lastSeq = message.seq
          this.needsResync = true
          this.scheduleFlush()
        }
        return
      default:
        this.receiveEvent(message)
    }
  }

  private receiveEvent(event: RealtimeEvent): void {
    if (event.seq <= this.lastSeq) return // duplicate or out of date
    if (event.seq > this.lastSeq + 1) this.needsResync = true // gap
    this.lastSeq = event.seq
    this.pending.push(event)
    this.scheduleFlush()
  }

  private scheduleFlush(): void {
    this.flushTimer ??= setTimeout(() => {
      this.flush()
    }, this.options.batchMs)
  }

  private flush(): void {
    if (this.flushTimer) clearTimeout(this.flushTimer)
    this.flushTimer = null
    const events = this.pending
    this.pending = []
    if (events.length > 0) this.options.onEvents(events)
    if (this.needsResync) {
      this.needsResync = false
      this.options.onResync()
    }
  }

  private handleClose(socket: SocketLike, code: number): void {
    if (socket !== this.socket) return
    this.socket = null
    this.detach(socket)
    // Events that arrived before the close are still valid.
    this.flush()
    if (this.stopped) return

    if (code === CLOSE_UNAUTHENTICATED) {
      this.stopped = true
      this.setStatus('offline')
      this.options.onUnauthorized()
      return
    }
    if (code === CLOSE_FORBIDDEN) {
      this.stopped = true
      this.setStatus('offline')
      return
    }
    this.scheduleReconnect()
  }

  private scheduleReconnect(): void {
    this.failedAttempts += 1
    const { initialBackoffMs, maxBackoffMs, random } = this.options
    const base = Math.min(maxBackoffMs, initialBackoffMs * 2 ** (this.failedAttempts - 1))
    // ±20% jitter so many clients don't reconnect in lockstep after a server restart.
    const delay = Math.round(base * (0.8 + 0.4 * random()))
    this.setStatus(this.retryStatus())
    this.reconnectTimer = setTimeout(() => {
      this.connect()
    }, delay)
  }

  private retryStatus(): ConnectionStatus {
    return !this.options.isOnline() || this.failedAttempts >= this.options.offlineAfterAttempts
      ? 'offline'
      : 'reconnecting'
  }

  private readonly handleOnline = (): void => {
    // Back online: don't wait for the backoff timer.
    if (!this.stopped && this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.connect()
    }
  }

  private detach(socket: SocketLike): void {
    socket.onopen = null
    socket.onmessage = null
    socket.onclose = null
    socket.onerror = null
  }

  private clearTimers(): void {
    if (this.flushTimer) clearTimeout(this.flushTimer)
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.flushTimer = null
    this.reconnectTimer = null
  }

  private setStatus(status: ConnectionStatus): void {
    if (status === this.status) return
    this.status = status
    for (const listener of this.listeners) listener(status)
  }
}
