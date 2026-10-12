/**
 * Accessibility checks with axe-core on the real pages, plus keyboard paths.
 * (Color contrast cannot be computed in jsdom, so that one rule is off here.)
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { describe, expect, it } from 'vitest'

import { dashboardHandlers, makeReservation } from './test/fixtures'
import { FRONT_DESK_USER, MANAGER_USER, mockApi, renderApp } from './test/renderApp'

const MANAGER = MANAGER_USER

async function expectNoViolations() {
  const results = await axe.run(document.body, {
    rules: { 'color-contrast': { enabled: false } },
  })
  const problems = results.violations.map(
    (v) =>
      `${v.id} (${v.impact ?? 'unknown'}): ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
  )
  expect(problems).toEqual([])
}

describe('accessibility (axe)', () => {
  it('login page, including the error state', async () => {
    mockApi({ 'POST /api/auth/login': () => [401, { detail: 'Invalid username or password' }] })
    renderApp('/login')
    await expectNoViolations()

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Username'), 'x')
    await user.type(screen.getByLabelText('Password'), 'y')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    await screen.findByText('Invalid username or password.')

    await expectNoViolations()
  })

  it('dashboard with summary, filters and room grid', async () => {
    mockApi({ 'GET /api/auth/me': () => [200, FRONT_DESK_USER], ...dashboardHandlers() })
    renderApp('/')
    await screen.findByRole('button', { name: /^Room 201,/ })
    await screen.findByRole('list', { name: "Today's summary" })

    await expectNoViolations()
  })

  it('dashboard with the detail panel open (manager actions)', async () => {
    mockApi({
      'GET /api/auth/me': () => [200, MANAGER],
      ...dashboardHandlers(),
      'GET /api/reservations/13': () => [200, makeReservation()],
    })
    renderApp('/?room=4')
    const panel = await screen.findByRole('complementary', { name: 'Room 201' })
    await within(panel).findByText('Alan Turing')

    await expectNoViolations()
  })

  it('activation page: form, validation errors and success', async () => {
    const token = 'x'.repeat(43)
    mockApi({
      [`GET /api/auth/activate/${token}`]: () => [
        200,
        { username: 'newbie', full_name: 'New Bie' },
      ],
      [`POST /api/auth/activate/${token}`]: () => [200, { detail: 'ok' }],
    })
    renderApp(`/activate/${token}`)
    await screen.findByRole('heading', { name: 'Welcome, New Bie' })
    await expectNoViolations()

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('New password'), 'short')
    await user.click(screen.getByRole('button', { name: 'Activate account' }))
    await expectNoViolations()

    await user.clear(screen.getByLabelText('New password'))
    await user.type(screen.getByLabelText('New password'), 'Tricky-Ferret-Lamp')
    await user.type(screen.getByLabelText('Confirm new password'), 'Tricky-Ferret-Lamp')
    await user.click(screen.getByRole('button', { name: 'Activate account' }))
    await screen.findByRole('heading', { name: 'Your account is ready' })
    await expectNoViolations()
  })

  it('expired reset link', async () => {
    const token = 'x'.repeat(43)
    mockApi({
      [`GET /api/auth/reset-password/${token}`]: () => [
        410,
        { detail: 'x', code: 'token_expired' },
      ],
    })
    renderApp(`/reset-password/${token}`)
    await screen.findByText('This link has expired.')
    await expectNoViolations()
  })

  it('forgot password page, invalid input and confirmation', async () => {
    mockApi({ 'POST /api/auth/forgot-password': () => [202, { detail: 'Sent if it exists.' }] })
    renderApp('/forgot-password')
    await expectNoViolations()

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Email address'), 'nope')
    await user.click(screen.getByRole('button', { name: 'Send reset link' }))
    await expectNoViolations()

    await user.type(screen.getByLabelText('Email address'), '@hotel.example')
    await user.click(screen.getByRole('button', { name: 'Send reset link' }))
    await screen.findByRole('heading', { name: 'Check your email' })
    await expectNoViolations()
  })

  it('not-found page', async () => {
    mockApi({})
    renderApp('/no/such/page')
    await screen.findByRole('heading', { name: 'Page not found' })

    await expectNoViolations()
  })
})

describe('keyboard', () => {
  it('login form is in a sensible tab order and submits with Enter', async () => {
    const fetchMock = mockApi({
      'POST /api/auth/login': () => [401, { detail: 'Invalid username or password' }],
    })
    renderApp('/login')
    const user = userEvent.setup()

    await user.tab()
    expect(screen.getByLabelText('Username')).toHaveFocus()
    await user.keyboard('frontdesk')
    await user.tab()
    expect(screen.getByLabelText('Password')).toHaveFocus()
    await user.keyboard('secret{Enter}')

    await screen.findByText('Invalid username or password.')
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('skip link moves focus past the header to the main content', async () => {
    mockApi({ 'GET /api/auth/me': () => [200, FRONT_DESK_USER], ...dashboardHandlers() })
    renderApp('/')
    await screen.findByRole('button', { name: /^Room 201,/ })
    const user = userEvent.setup()

    await user.tab()
    const skip = screen.getByRole('link', { name: 'Skip to main content' })
    expect(skip).toHaveFocus()
    await user.click(skip)

    await waitFor(() => {
      expect(screen.getByRole('main')).toHaveFocus()
    })
  })

  it('a room tile opens the panel with Enter and Escape returns to the tile', async () => {
    mockApi({
      'GET /api/auth/me': () => [200, FRONT_DESK_USER],
      ...dashboardHandlers(),
      'GET /api/reservations/13': () => [200, makeReservation()],
    })
    renderApp('/')
    const tile = await screen.findByRole('button', { name: /^Room 201,/ })
    const user = userEvent.setup()

    tile.focus()
    await user.keyboard('{Enter}')
    const panel = await screen.findByRole('complementary', { name: 'Room 201' })
    expect(within(panel).getByRole('heading', { name: 'Room 201' })).toHaveFocus()

    await user.keyboard('{Escape}')
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^Room 201,/ })).toHaveFocus()
    })
  })
})
