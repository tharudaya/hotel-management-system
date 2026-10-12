/**
 * Messages sent by the server on /ws (see server/app/realtime/hub.py for the protocol).
 * The WebSocket is not part of the OpenAPI schema, so the envelopes are declared here;
 * the payloads reuse the generated REST types.
 */
import type { DashboardRoom, DashboardSummary } from '../api/types'

export interface HelloMessage {
  type: 'hello'
  seq: number
  payload: { heartbeat_seconds: number }
}

export interface PingMessage {
  type: 'ping'
  seq: number
}

export interface RoomStatusChangedMessage {
  type: 'room_status_changed'
  seq: number
  payload: { business_date: string; room: DashboardRoom }
}

export interface SummaryChangedMessage {
  type: 'summary_changed'
  seq: number
  payload: { summary: DashboardSummary }
}

/** Broadcast events: the only messages that consume a sequence number. */
export type RealtimeEvent = RoomStatusChangedMessage | SummaryChangedMessage

export type ServerMessage = HelloMessage | PingMessage | RealtimeEvent

/** Close codes with meaning to the client. */
export const CLOSE_UNAUTHENTICATED = 4401
export const CLOSE_FORBIDDEN = 4403

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** Parse and minimally validate a server message; unknown or malformed ones yield null. */
export function parseServerMessage(text: string): ServerMessage | null {
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    return null
  }
  if (!isRecord(data) || typeof data.seq !== 'number' || !Number.isInteger(data.seq)) return null
  const { type, seq, payload } = data

  switch (type) {
    case 'ping':
      return { type, seq }
    case 'hello':
      if (isRecord(payload) && typeof payload.heartbeat_seconds === 'number') {
        return { type, seq, payload: { heartbeat_seconds: payload.heartbeat_seconds } }
      }
      return null
    case 'room_status_changed':
      if (
        isRecord(payload) &&
        typeof payload.business_date === 'string' &&
        isRecord(payload.room) &&
        typeof payload.room.room_id === 'number' &&
        typeof payload.room.version === 'number'
      ) {
        // The server is trusted for the remaining fields (same schema as GET /rooms).
        return data as unknown as RoomStatusChangedMessage
      }
      return null
    case 'summary_changed':
      if (isRecord(payload) && isRecord(payload.summary)) {
        return data as unknown as SummaryChangedMessage
      }
      return null
    default:
      return null
  }
}
