import type { SocketLike } from '../realtime/connection'

/**
 * Stand-in for the browser WebSocket. Installed globally in test setup so components
 * never open real connections; tests drive it from the "server" side.
 */
export class FakeWebSocket implements SocketLike {
  static instances: FakeWebSocket[] = []

  /** The most recently created socket. */
  static latest(): FakeWebSocket {
    const socket = FakeWebSocket.instances.at(-1)
    if (!socket) throw new Error('No WebSocket was opened')
    return socket
  }

  static reset(): void {
    FakeWebSocket.instances = []
  }

  readonly url: string
  readonly sent: unknown[] = []
  closed: { code: number | undefined; reason: string | undefined } | null = null
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  send(data: string): void {
    this.sent.push(JSON.parse(data))
  }

  close(code?: number, reason?: string): void {
    this.closed = { code, reason }
  }

  // --- server side ---

  receive(message: unknown): void {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(message) }))
  }

  hello(seq = 0): void {
    this.receive({ type: 'hello', seq, payload: { heartbeat_seconds: 25 } })
  }

  serverClose(code: number, reason = ''): void {
    this.onclose?.(new CloseEvent('close', { code, reason }))
  }
}
