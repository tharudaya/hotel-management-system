import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { mockApi, renderApp } from '../test/renderApp'

const TOKEN = 'Abc123_-Abc123_-Abc123_-Abc123_-Abc123_-xyz'
const ACTIVATE = `/api/auth/activate/${TOKEN}`
const RESET = `/api/auth/reset-password/${TOKEN}`
const NEWBIE = { username: 'newbie', full_name: 'New Bie' }
const GOOD = 'Tricky-Ferret-Lamp'

async function choosePassword(password: string, confirm = password, button = 'Activate account') {
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('New password'), password)
  await user.type(screen.getByLabelText('Confirm new password'), confirm)
  await user.click(screen.getByRole('button', { name: button }))
}

describe('activate account', () => {
  it('shows who the invite is for, sets the password and links to sign in', async () => {
    const fetchMock = mockApi({
      [`GET ${ACTIVATE}`]: () => [200, NEWBIE],
      [`POST ${ACTIVATE}`]: () => [200, { detail: 'Your account is ready. You can now sign in.' }],
    })
    const router = renderApp(`/activate/${TOKEN}`)

    expect(screen.getByRole('status')).toHaveTextContent('Checking your link…')
    expect(await screen.findByRole('heading', { name: 'Welcome, New Bie' })).toBeInTheDocument()
    expect(screen.getByText('newbie')).toBeInTheDocument()
    expect(screen.getByLabelText('New password')).toHaveAccessibleDescription(
      /At least 12 characters/,
    )

    await choosePassword(GOOD)

    expect(
      await screen.findByRole('heading', { name: 'Your account is ready' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/You can now sign in/)).toHaveFocus()
    const [, init] = fetchMock.mock.calls[1] ?? []
    expect(init?.body).toBe(JSON.stringify({ password: GOOD }))
    // Not signed in automatically: the way on is the sign-in page.
    await userEvent.setup().click(screen.getByRole('link', { name: 'Go to sign in' }))
    expect(router.state.location.pathname).toBe('/login')
  })

  it('checks length and confirmation before sending anything', async () => {
    const fetchMock = mockApi({ [`GET ${ACTIVATE}`]: () => [200, NEWBIE] })
    renderApp(`/activate/${TOKEN}`)

    await choosePassword('short', 'short')

    const password = screen.getByLabelText('New password')
    expect(password).toHaveAttribute('aria-invalid', 'true')
    expect(password).toHaveAccessibleDescription(/^Use at least 12 characters\./)
    expect(password).toHaveFocus()

    const user = userEvent.setup()
    await user.clear(password)
    await user.type(password, GOOD)
    expect(password).not.toHaveAttribute('aria-invalid')
    await user.click(screen.getByRole('button', { name: 'Activate account' }))

    const confirm = screen.getByLabelText('Confirm new password')
    expect(confirm).toHaveAccessibleDescription('The passwords do not match.')
    expect(confirm).toHaveFocus()
    expect(fetchMock).toHaveBeenCalledOnce() // only the link check
  })

  it('shows the server password rule on the field and keeps the form', async () => {
    mockApi({
      [`GET ${ACTIVATE}`]: () => [200, NEWBIE],
      [`POST ${ACTIVATE}`]: () => [
        422,
        { detail: 'This password is too common. Choose another.', code: 'password_too_common' },
      ],
    })
    renderApp(`/activate/${TOKEN}`)

    await choosePassword('password2024!')

    const password = screen.getByLabelText('New password')
    await waitFor(() => {
      expect(password).toHaveAccessibleDescription(/^This password is too common/)
    })
    expect(password).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Activate account' })).toBeEnabled()
  })

  it.each([
    [404, 'token_invalid', 'This link is not valid.'],
    [410, 'token_expired', 'This link has expired.'],
    [410, 'token_used', 'This link has already been used.'],
  ])('explains a bad link (%i %s)', async (status, code, message) => {
    mockApi({ [`GET ${ACTIVATE}`]: () => [status, { detail: 'x', code }] })
    renderApp(`/activate/${TOKEN}`)

    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(screen.getByText('Ask your manager to send you a new invite.')).toBeInTheDocument()
    expect(screen.queryByLabelText('New password')).not.toBeInTheDocument()
  })

  it('switches to the bad-link message if the link dies before submitting', async () => {
    mockApi({
      [`GET ${ACTIVATE}`]: () => [200, NEWBIE],
      [`POST ${ACTIVATE}`]: () => [410, { detail: 'x', code: 'token_used' }],
    })
    renderApp(`/activate/${TOKEN}`)

    await choosePassword(GOOD)

    expect(await screen.findByRole('alert')).toHaveTextContent('This link has already been used.')
  })

  it('offers a retry when the link cannot be checked', async () => {
    let calls = 0
    mockApi({
      [`GET ${ACTIVATE}`]: () => (++calls === 1 ? [503, { detail: 'down' }] : [200, NEWBIE]),
    })
    renderApp(`/activate/${TOKEN}`)

    await userEvent.setup().click(await screen.findByRole('button', { name: 'Try again' }))

    expect(await screen.findByRole('heading', { name: 'Welcome, New Bie' })).toBeInTheDocument()
  })
})

describe('reset password', () => {
  it('sets a new password and says other sessions were signed out', async () => {
    mockApi({
      [`GET ${RESET}`]: () => [200, NEWBIE],
      [`POST ${RESET}`]: () => [200, { detail: 'Your password has been changed.' }],
    })
    renderApp(`/reset-password/${TOKEN}`)

    expect(await screen.findByRole('heading', { name: 'Hello, New Bie' })).toBeInTheDocument()
    await choosePassword(GOOD, GOOD, 'Change password')

    expect(await screen.findByRole('heading', { name: 'Password changed' })).toBeInTheDocument()
    expect(screen.getByText(/signed out/)).toHaveFocus()
  })

  it('points to a new reset link when the link has expired', async () => {
    mockApi({ [`GET ${RESET}`]: () => [410, { detail: 'x', code: 'token_expired' }] })
    const router = renderApp(`/reset-password/${TOKEN}`)

    expect(await screen.findByRole('alert')).toHaveTextContent('This link has expired.')
    await userEvent.setup().click(screen.getByRole('link', { name: 'Request a new reset link' }))
    expect(router.state.location.pathname).toBe('/forgot-password')
  })
})

describe('forgot password', () => {
  const ANSWER = 'If an account with that email address exists, we have sent a link.'

  it('is reachable from the login page and sends the request', async () => {
    const fetchMock = mockApi({ 'POST /api/auth/forgot-password': () => [202, { detail: ANSWER }] })
    renderApp('/login')
    const user = userEvent.setup()

    await user.click(screen.getByRole('link', { name: 'Forgot your password?' }))
    await user.type(screen.getByLabelText('Email address'), ' alice@hotel.example ')
    await user.click(screen.getByRole('button', { name: 'Send reset link' }))

    expect(await screen.findByRole('heading', { name: 'Check your email' })).toBeInTheDocument()
    expect(screen.getByText(ANSWER)).toHaveFocus()
    const [, init] = fetchMock.mock.calls[0] ?? []
    expect(init?.body).toBe('{"email":"alice@hotel.example"}')
  })

  it('validates the address without sending', async () => {
    const fetchMock = mockApi({})
    renderApp('/forgot-password')
    const user = userEvent.setup()

    await user.type(screen.getByLabelText('Email address'), 'alice')
    await user.click(screen.getByRole('button', { name: 'Send reset link' }))

    const email = screen.getByLabelText('Email address')
    expect(email).toHaveAttribute('aria-invalid', 'true')
    expect(email).toHaveAccessibleDescription(/^Enter a valid email address/)
    expect(email).toHaveFocus()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('shows a generic error when the request fails', async () => {
    mockApi({ 'POST /api/auth/forgot-password': () => [500, { detail: 'boom' }] })
    renderApp('/forgot-password')
    const user = userEvent.setup()

    await user.type(screen.getByLabelText('Email address'), 'alice@hotel.example')
    await user.click(screen.getByRole('button', { name: 'Send reset link' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Something went wrong. Please try again.',
    )
  })

  it('links back to sign in', async () => {
    mockApi({})
    const router = renderApp('/forgot-password')

    await userEvent.setup().click(screen.getByRole('link', { name: 'Back to sign in' }))

    expect(router.state.location.pathname).toBe('/login')
  })
})
