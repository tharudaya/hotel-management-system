import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { dashboardHandlers } from '../test/fixtures'
import { FRONT_DESK_USER, mockApi, renderApp } from '../test/renderApp'
import { redirectTarget } from './currentUser'

async function fillAndSubmit(username: string, password: string) {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Username'), username)
  await user.type(screen.getByLabelText('Password'), password)
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
}

describe('login', () => {
  it('signs in and shows the dashboard with the user in the header', async () => {
    const fetchMock = mockApi({
      'POST /api/auth/login': () => [200, FRONT_DESK_USER],
      ...dashboardHandlers(),
    })
    const router = renderApp('/login')

    await fillAndSubmit('frontdesk', 'frontdesk-dev-pass')

    expect(await screen.findByRole('heading', { name: 'Rooms' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/')
    expect(screen.getByRole('banner')).toHaveTextContent('Fran Desk')
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()

    const [path, init] = fetchMock.mock.calls[0] ?? []
    expect(path).toBe('/api/auth/login')
    expect(init?.credentials).toBe('include')
    expect(init?.body).toBe('{"username":"frontdesk","password":"frontdesk-dev-pass"}')
    // The login response seeds the current user, so /me is not fetched again.
    expect(fetchMock.mock.calls.map(([p]) => p)).not.toContain('/api/auth/me')
  })

  it('announces a generic error and stays on the page when credentials are wrong', async () => {
    mockApi({
      'POST /api/auth/login': () => [401, { detail: 'Invalid username or password' }],
    })
    const router = renderApp('/login')

    await fillAndSubmit('frontdesk', 'wrong')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Invalid username or password.')
    expect(screen.getByLabelText('Username')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('Password')).toHaveAccessibleDescription(
      'Invalid username or password.',
    )
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled()
    expect(router.state.location.pathname).toBe('/login')
  })

  it('shows a generic message for unexpected failures without leaking server details', async () => {
    mockApi({
      'POST /api/auth/login': () => [500, { detail: 'OperationalError at db:5432' }],
    })
    renderApp('/login')

    await fillAndSubmit('frontdesk', 'frontdesk-dev-pass')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Sign in failed. Please try again.')
    expect(alert).not.toHaveTextContent('OperationalError')
  })

  it('explains throttling', async () => {
    mockApi({ 'POST /api/auth/login': () => [429, { detail: 'Too many failed login attempts.' }] })
    renderApp('/login')

    await fillAndSubmit('frontdesk', 'x')

    expect(await screen.findByRole('alert')).toHaveTextContent('Too many failed attempts')
  })

  it('disables the submit button while the request is pending', async () => {
    let respond: (reply: [number, unknown]) => void = () => undefined
    mockApi({
      'POST /api/auth/login': () =>
        new Promise((resolve) => {
          respond = resolve
        }),
    })
    renderApp('/login')

    await fillAndSubmit('frontdesk', 'frontdesk-dev-pass')

    expect(screen.getByRole('button', { name: 'Signing in…' })).toBeDisabled()
    respond([401, { detail: 'Invalid username or password' }])
    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeEnabled()
  })

  it('starts with an empty, silent alert region', () => {
    mockApi({})
    renderApp('/login')

    expect(screen.getByRole('alert')).toBeEmptyDOMElement()
    expect(screen.getByLabelText('Username')).not.toHaveAttribute('aria-invalid')
  })
})

describe('protected routes', () => {
  it('redirects to /login when not authenticated, then returns after login', async () => {
    mockApi({
      'GET /api/auth/me': () => [401, { detail: 'Not authenticated' }],
      'POST /api/auth/login': () => [200, FRONT_DESK_USER],
      ...dashboardHandlers(),
    })
    const router = renderApp('/?floor=2')

    expect(
      await screen.findByRole('heading', { name: 'Sign in to Front Desk' }),
    ).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/login')
    expect(screen.queryByRole('banner')).not.toBeInTheDocument()

    await fillAndSubmit('frontdesk', 'frontdesk-dev-pass')

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/')
    })
    expect(router.state.location.search).toBe('?floor=2')
  })

  it('renders the page for an authenticated user', async () => {
    mockApi({
      'GET /api/auth/me': () => [200, FRONT_DESK_USER],
      ...dashboardHandlers(),
    })
    renderApp('/')

    expect(await screen.findByRole('heading', { name: 'Rooms' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /^Room 101,/ })).toBeInTheDocument()
  })

  it('does not send the user to login when the server is unreachable', async () => {
    mockApi({ 'GET /api/auth/me': () => [503, { detail: 'down' }] })
    const router = renderApp('/')

    expect(await screen.findByText('Could not reach the server.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/')
  })
})

describe('logout', () => {
  it('signs out from the header and returns to the login page', async () => {
    const fetchMock = mockApi({
      'GET /api/auth/me': () => [200, FRONT_DESK_USER],
      ...dashboardHandlers(),
      'POST /api/auth/logout': () => [204],
    })
    const router = renderApp('/')

    await userEvent.setup().click(await screen.findByRole('button', { name: 'Sign out' }))

    expect(
      await screen.findByRole('heading', { name: 'Sign in to Front Desk' }),
    ).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/login')
    expect(
      fetchMock.mock.calls.some(([p, init]) => p === '/api/auth/logout' && init?.method === 'POST'),
    ).toBe(true)
  })
})

describe('redirectTarget', () => {
  it.each([
    [{ from: '/?floor=2' }, '/?floor=2'],
    [{ from: '//evil.example' }, '/'],
    [{ from: 'https://evil.example' }, '/'],
    [{ from: 42 }, '/'],
    [null, '/'],
    ['/x', '/'],
  ])('%j -> %s', (state, expected) => {
    expect(redirectTarget(state)).toBe(expected)
  })
})
