/**
 * Minimal fetch wrapper for the backend API.
 *
 * - Always sends cookies (the session lives in an HTTP-only cookie the code never sees).
 * - Paths are relative ("/api/..."): in development the Vite proxy forwards them.
 * - A 401 calls the unauthorized handler (redirect to /login) unless the caller opts out,
 *   e.g. the login request itself, where 401 just means "wrong password".
 * - Non-2xx responses throw ApiError carrying the server's message and error code.
 */

export class ApiError extends Error {
  readonly status: number
  /** Machine-readable code from the backend (e.g. "version_conflict"), if any. */
  readonly code: string | undefined
  /** The whole JSON error body, for extra fields (e.g. unavailable_nights). */
  readonly body: unknown

  constructor(status: number, message: string, code?: string, body: unknown = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.body = body
  }
}

type UnauthorizedHandler = () => void

let onUnauthorized: UnauthorizedHandler = () => {
  if (window.location.pathname !== '/login') window.location.assign('/login')
}

/** Replace the default 401 behaviour (full page redirect) with an in-app one. */
export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  onUnauthorized = handler
}

/** Report a lost session from outside apiFetch (e.g. the WebSocket closed with 4401). */
export function notifyUnauthorized(): void {
  onUnauthorized()
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  /** Serialized as JSON. */
  body?: unknown
  signal?: AbortSignal
  /** Default true. Set false where a 401 is an expected answer, not a lost session. */
  redirectOnUnauthorized?: boolean
  /** Extra request headers (e.g. Idempotency-Key). */
  headers?: Record<string, string>
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, redirectOnUnauthorized = true } = options
  const headers: Record<string, string> = { Accept: 'application/json', ...options.headers }
  const init: RequestInit = { method, headers, credentials: 'include' }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }
  if (signal) init.signal = signal

  const response = await fetch(path, init)

  if (response.status === 401 && redirectOnUnauthorized) onUnauthorized()
  if (!response.ok) throw await toApiError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    // Not JSON (e.g. a proxy error page); fall back to the status text.
  }
  return new ApiError(response.status, errorMessage(body, response), errorCode(body), body)
}

function errorMessage(body: unknown, response: Response): string {
  if (isRecord(body)) {
    const { detail } = body
    if (typeof detail === 'string') return detail
    // FastAPI validation errors: detail is a list of { msg, loc, ... }.
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item: unknown) => (isRecord(item) && typeof item.msg === 'string' ? item.msg : null))
        .filter((msg): msg is string => msg !== null)
      if (messages.length > 0) return messages.join('; ')
    }
  }
  return response.statusText || `Request failed with status ${response.status}`
}

function errorCode(body: unknown): string | undefined {
  return isRecord(body) && typeof body.code === 'string' ? body.code : undefined
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
