/**
 * Reservations in a real browser against the running app (see playwright.config.ts).
 */
import { expect, test } from '@playwright/test'

import {
  addDays,
  api,
  book,
  businessDate,
  fillBooking,
  roomType,
  signIn,
  USERS,
  window,
} from './support.ts'

test('a phone booking, then check-in and check-out from the dashboard', async ({ page }) => {
  const type = await roomType(2, 'Phone room')
  await signIn(page, USERS.receptionist)

  // Book: Reservations -> New reservation -> ... -> confirm.
  await page
    .getByRole('navigation', { name: 'Main' })
    .getByRole('link', { name: 'Reservations' })
    .click()
  await page.getByRole('link', { name: 'New reservation' }).click()
  await expect(page.getByRole('heading', { name: 'Dates and guests' })).toBeVisible()
  await expect(page.getByText('1 night')).toBeVisible()
  await page.getByRole('button', { name: 'Continue' }).click()
  const radio = page.getByRole('radio', { name: new RegExp(type.name) })
  await expect(radio.locator('xpath=ancestor::label')).toContainText('2 rooms available')
  await radio.check()
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByRole('radio', { name: 'New guest' }).check()
  await page.getByLabel('First name').fill('Phoebe')
  await page.getByLabel('Last name').fill('Caller')
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByLabel('Booked by').selectOption('phone')
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByText('SEK 1,500.00 total')).toBeVisible()
  await page.getByRole('button', { name: 'Confirm booking' }).click()

  await expect(page.getByRole('heading', { name: 'Booking confirmed' })).toBeVisible()
  const code = (await page.getByLabel(/^Confirmation code/).textContent())?.replace(/\s/g, '')
  expect(code).toMatch(/^[2-9A-HJKMNP-Z]{8}$/)

  // Check in from the reservation, into the first room of the type.
  await page.getByRole('link', { name: 'Open the reservation' }).click()
  await expect(page.getByRole('heading', { name: 'Phoebe Caller', level: 1 })).toBeVisible()
  await page.getByRole('button', { name: 'Check in' }).click()
  const dialog = page.getByRole('dialog', { name: /Check in Phoebe Caller/ })
  const [room] = type.rooms
  await dialog.getByRole('combobox', { name: 'Room' }).selectOption(String(room.id))
  await dialog.getByRole('button', { name: 'Check in' }).click()
  await expect(page.getByText(`Phoebe Caller is checked in to room ${room.number}.`)).toBeVisible()
  await expect(page.getByText('Checked in', { exact: true }).first()).toBeVisible()

  // Check out from the dashboard: the room's tile, its panel, "Check out early".
  await page
    .getByRole('navigation', { name: 'Main' })
    .getByRole('link', { name: 'Dashboard' })
    .click()
  await page.getByRole('button', { name: new RegExp(`^Room ${room.number},`) }).click()
  const panel = page.getByRole('complementary', { name: `Room ${room.number}` })
  await expect(panel.getByText('Phoebe Caller')).toBeVisible()
  await panel.getByRole('button', { name: /^Check out/ }).click()
  await expect(panel.getByText('Vacant')).toBeVisible()
  await expect(panel.getByText('Dirty')).toBeVisible()

  // The reservation agrees.
  const desk = await api(USERS.receptionist)
  const found = (await (await desk.get(`/api/reservations/by-code/${code ?? ''}`)).json()) as {
    status: string
  }
  expect(found.status).toBe('checked_out')
  await desk.dispose()
})

test('two people booking the last room at once: one gets it, the other is told', async ({
  browser,
}) => {
  const type = await roomType(1, 'Last room')
  const [desk, manager] = await Promise.all([
    window(browser, USERS.receptionist),
    window(browser, USERS.frontOfficeManager),
  ])
  await Promise.all([fillBooking(desk, type, 'First'), fillBooking(manager, type, 'Second')])

  // Both press confirm at the same time.
  await Promise.all([
    desk.getByRole('button', { name: 'Confirm booking' }).click(),
    manager.getByRole('button', { name: 'Confirm booking' }).click(),
  ])

  const outcome = async (page: typeof desk) => {
    const won = page.getByRole('heading', { name: 'Booking confirmed' })
    const lost = page.getByText(/While you were booking, .* filled up/)
    await expect(won.or(lost)).toBeVisible()
    return (await won.isVisible()) ? 'booked' : 'told'
  }
  const results = [await outcome(desk), await outcome(manager)].sort()
  expect(results).toEqual(['booked', 'told'])

  // The one who was told sees the type as full now, with everything else kept.
  const loser = (await desk.getByRole('heading', { name: 'Booking confirmed' }).isVisible())
    ? manager
    : desk
  await expect(loser.getByRole('radio', { name: new RegExp(type.name) })).toBeDisabled()
  await desk.context().close()
  await manager.context().close()
})

test('change dates, cancel and mark a no-show', async ({ page }) => {
  const type = await roomType(2, 'Changes')
  const manager = await api(USERS.frontOfficeManager)
  const today = await businessDate(manager)
  const later = await book(manager, type, addDays(today, 3), addDays(today, 5), 'Mover')
  const cancelled = await book(manager, type, addDays(today, 6), addDays(today, 7), 'Canceller')
  const noShow = await book(manager, type, today, addDays(today, 1), 'Absent')
  await manager.dispose()
  await signIn(page, USERS.frontOfficeManager)

  // Change: one more night; the price difference shows before saving.
  await page.goto(`/reservations/${later.id}`)
  await page.getByRole('link', { name: 'Change booking' }).click()
  await page.getByLabel('Departure').fill(addDays(today, 6))
  const preview = page.getByRole('complementary', { name: 'Price before and after' })
  await expect(preview).toContainText('Up SEK 1,500.00')
  await page.getByRole('button', { name: 'Save changes' }).click()
  await expect(page.getByText(`${later.confirmation_code} has been changed.`)).toBeVisible()
  await expect(page.getByRole('region', { name: 'Stay' })).toContainText('3')

  // Cancel, with a reason, after a confirmation naming the guest and code.
  await page.goto(`/reservations/${cancelled.id}`)
  await page.getByRole('button', { name: 'Cancel booking' }).click()
  const cancel = page.getByRole('dialog', { name: "Cancel E2E Canceller's booking?" })
  await expect(cancel).toContainText(cancelled.confirmation_code)
  await cancel.getByLabel('Reason').fill('Guest called to cancel')
  await cancel.getByRole('button', { name: 'Cancel booking' }).click()
  await expect(page.getByText(/has been cancelled\./)).toBeVisible()
  await expect(page.getByRole('region', { name: 'History' })).toContainText(
    'Reason: Guest called to cancel',
  )

  // No-show: today's arrival who did not come.
  await page.goto(`/reservations/${noShow.id}`)
  await page.getByRole('button', { name: 'Mark no-show' }).click()
  const absent = page.getByRole('dialog', { name: 'Mark E2E Absent as a no-show?' })
  await absent.getByRole('button', { name: 'Mark no-show' }).click()
  await expect(page.getByText(/has been marked as a no-show\./)).toBeVisible()
  await expect(page.getByText('No-show', { exact: true }).first()).toBeVisible()
})

test('a receptionist cannot set prices or cancel; a front office manager can', async ({
  browser,
}) => {
  const type = await roomType(1, 'Rights')
  const manager = await api(USERS.frontOfficeManager)
  const today = await businessDate(manager)
  const booking = await book(manager, type, addDays(today, 10), addDays(today, 11), 'Rights')
  await manager.dispose()

  for (const [user, allowed] of [
    [USERS.receptionist, false],
    [USERS.frontOfficeManager, true],
  ] as const) {
    const page = await window(browser, user)
    // The price per night in the booking flow.
    await fillBooking(page, type, 'Rights').catch(() => undefined)
    await page.getByRole('button', { name: 'Back' }).click()
    await expect(page.getByRole('heading', { name: 'Details' })).toBeVisible()
    await expect(page.getByLabel(/Price per night/)).toHaveCount(allowed ? 1 : 0)
    // Cancelling a booking.
    await page.goto(`/reservations/${booking.id}`)
    await expect(page.getByRole('group', { name: 'Actions' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Cancel booking' })).toHaveCount(allowed ? 1 : 0)
    await expect(page.getByRole('link', { name: 'Change booking' })).toHaveCount(1)
    await page.context().close()
  }
})
