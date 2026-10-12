/** Loading and error states that aren't covered by the feature tests. */
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { BUSINESS_DATE, HOTEL, makeReservation, ROOMS, SUMMARY } from './test/fixtures'
import { FRONT_DESK_USER, mockApi, renderApp } from './test/renderApp'

describe('dashboard loading and error states', () => {
  it('shows a summary error with retry while the grid still works', async () => {
    let summaryFails = true
    mockApi({
      'GET /api/auth/me': () => [200, FRONT_DESK_USER],
      'GET /api/hotel': () => [200, HOTEL],
      'GET /api/dashboard/rooms': () => [200, { business_date: BUSINESS_DATE, rooms: ROOMS }],
      'GET /api/dashboard/summary': () => (summaryFails ? [500, {}] : [200, SUMMARY]),
    })
    renderApp('/')

    expect(
      await screen.findByText("Could not load today's summary.", {}, { timeout: 4000 }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Room 201,/ })).toBeInTheDocument()

    summaryFails = false
    await userEvent.setup().click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('list', { name: "Today's summary" })).toBeInTheDocument()
  })

  it('shows a room list error with retry', async () => {
    let roomsFail = true
    mockApi({
      'GET /api/auth/me': () => [200, FRONT_DESK_USER],
      'GET /api/hotel': () => [200, HOTEL],
      'GET /api/dashboard/rooms': () =>
        roomsFail ? [500, {}] : [200, { business_date: BUSINESS_DATE, rooms: ROOMS }],
      'GET /api/dashboard/summary': () => [200, SUMMARY],
    })
    renderApp('/')

    expect(
      await screen.findByText('Could not load the rooms.', {}, { timeout: 4000 }),
    ).toBeInTheDocument()

    roomsFail = false
    await userEvent.setup().click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('button', { name: /^Room 201,/ })).toBeInTheDocument()
  })

  it('shows a reservation error with retry in the detail panel', async () => {
    let detailFails = true
    mockApi({
      'GET /api/auth/me': () => [200, FRONT_DESK_USER],
      'GET /api/hotel': () => [200, HOTEL],
      'GET /api/dashboard/rooms': () => [200, { business_date: BUSINESS_DATE, rooms: ROOMS }],
      'GET /api/dashboard/summary': () => [200, SUMMARY],
      'GET /api/reservations/13': () => (detailFails ? [500, {}] : [200, makeReservation()]),
    })
    renderApp('/?room=4')
    const panel = await screen.findByRole('complementary', { name: 'Room 201' })

    expect(
      await within(panel).findByText(
        'Could not load the reservation details.',
        {},
        { timeout: 4000 },
      ),
    ).toBeInTheDocument()

    detailFails = false
    await userEvent.setup().click(within(panel).getByRole('button', { name: 'Retry' }))

    expect(await within(panel).findByText('Alan Turing')).toBeInTheDocument()
  })

  it('shows a friendly page for unknown addresses', async () => {
    mockApi({})
    renderApp('/rooms/42/edit')

    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to the dashboard' })).toHaveAttribute('href', '/')
  })
})
