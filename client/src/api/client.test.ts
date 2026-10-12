import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiFetch, setUnauthorizedHandler } from './client'

function stubFetch(status: number, body?: unknown) {
  const fetchMock = vi.fn<typeof fetch>(() =>
    Promise.resolve(
      new Response(body === undefined ? null : JSON.stringify(body), {
        status,
        headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      }),
    ),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('apiFetch', () => {
  const onUnauthorized = vi.fn()
  setUnauthorizedHandler(onUnauthorized)

  afterEach(() => {
    onUnauthorized.mockReset()
  })

  it('always sends credentials and JSON', async () => {
    const fetchMock = stubFetch(200, { status: 'ok' })

    const result = await apiFetch<{ status: string }>('/api/x', {
      method: 'POST',
      body: { a: 1 },
    })

    expect(result).toEqual({ status: 'ok' })
    const [path, init] = fetchMock.mock.calls[0] ?? []
    expect(path).toBe('/api/x')
    expect(init?.credentials).toBe('include')
    expect(init?.body).toBe('{"a":1}')
    expect(init?.headers).toMatchObject({ 'Content-Type': 'application/json' })
  })

  it('calls the unauthorized handler on 401 and throws', async () => {
    stubFetch(401, { detail: 'Not authenticated' })

    await expect(apiFetch('/api/auth/me')).rejects.toMatchObject({
      status: 401,
      message: 'Not authenticated',
    })
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('does not redirect when the caller expects a 401', async () => {
    stubFetch(401, { detail: 'Invalid username or password' })

    await expect(
      apiFetch('/api/auth/login', { method: 'POST', redirectOnUnauthorized: false }),
    ).rejects.toBeInstanceOf(ApiError)
    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('exposes the server error code', async () => {
    stubFetch(409, { detail: 'Room 101 was changed', code: 'version_conflict' })

    await expect(apiFetch('/api/rooms/1/housekeeping-status')).rejects.toMatchObject({
      status: 409,
      code: 'version_conflict',
      message: 'Room 101 was changed',
    })
  })

  it('returns undefined for 204', async () => {
    stubFetch(204)

    await expect(apiFetch('/api/auth/logout', { method: 'POST' })).resolves.toBeUndefined()
  })
})
