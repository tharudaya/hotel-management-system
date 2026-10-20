/** Helpers for the browser tests: signing in, and test data made through the API. */
import { type APIRequestContext, type Browser, expect, type Page, request } from '@playwright/test'

export const BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:5173'

/** Dev users from the seed script, by role. */
export const USERS = {
  admin: 'admin',
  receptionist: 'frontdesk',
  frontOfficeManager: 'fomanager',
} as const

export type DevUser = (typeof USERS)[keyof typeof USERS]

/** Sign in through the login page, as staff do. */
export async function signIn(page: Page, username: DevUser): Promise<void> {
  await page.goto('/login')
  await page.getByLabel('Username').fill(username)
  await page.getByLabel('Password').fill(`${username}-dev-pass`)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
}

/** A new browser window (its own cookies), signed in. */
export async function window(browser: Browser, username: DevUser): Promise<Page> {
  const context = await browser.newContext()
  const page = await context.newPage()
  await signIn(page, username)
  return page
}

/** An API client signed in as `username` (through the Vite proxy, like the app). */
export async function api(username: DevUser): Promise<APIRequestContext> {
  const context = await request.newContext({
    baseURL: BASE_URL,
    // State-changing requests must come from an allowed origin.
    extraHTTPHeaders: { Origin: BASE_URL },
  })
  const response = await context.post('/api/auth/login', {
    data: { username, password: `${username}-dev-pass` },
  })
  expect(response.ok(), await response.text()).toBe(true)
  return context
}

async function json<T>(response: Awaited<ReturnType<APIRequestContext['get']>>): Promise<T> {
  expect(response.ok(), `${response.url()}: ${await response.text()}`).toBe(true)
  return (await response.json()) as T
}

export async function businessDate(client: APIRequestContext): Promise<string> {
  return (await json<{ business_date: string }>(await client.get('/api/hotel'))).business_date
}

export function addDays(isoDate: string, days: number): string {
  const date = new Date(`${isoDate}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

export interface TestRoomType {
  id: number
  code: string
  name: string
  rooms: { id: number; number: string }[]
}

/**
 * A room type of its own for one test (unique code and name), priced 1 500.00 a night,
 * with `rooms` ready rooms. Made as admin.
 */
export async function roomType(rooms: number, label: string): Promise<TestRoomType> {
  const admin = await api(USERS.admin)
  const tag = Math.random().toString(36).slice(2, 7).toUpperCase()
  const created = await json<{ id: number; code: string; name: string }>(
    await admin.post('/api/admin/room-types', {
      data: {
        code: `E${tag}`,
        name: `${label} ${tag}`,
        max_occupancy: 2,
        base_rate_minor: 150_000,
      },
    }),
  )
  const made: TestRoomType['rooms'] = []
  for (let i = 1; i <= rooms; i++) {
    const room = await json<{ id: number; number: string }>(
      await admin.post('/api/admin/rooms', {
        data: { number: `${tag}${i}`, floor: 9, room_type_id: created.id },
      }),
    )
    made.push({ id: room.id, number: room.number })
  }
  await admin.dispose()
  return { id: created.id, code: created.code, name: created.name, rooms: made }
}

export interface Booking {
  id: number
  confirmation_code: string
  version: number
}

export async function book(
  client: APIRequestContext,
  type: TestRoomType,
  checkIn: string,
  checkOut: string,
  lastName: string,
): Promise<Booking> {
  return json<Booking>(
    await client.post('/api/reservations', {
      headers: { 'Idempotency-Key': crypto.randomUUID().replaceAll('-', '') },
      data: {
        guest: { first_name: 'E2E', last_name: lastName },
        room_type_id: type.id,
        check_in_date: checkIn,
        check_out_date: checkOut,
        source: 'phone',
      },
    }),
  )
}

/** The booking flow up to the review step, for a new guest. */
export async function fillBooking(page: Page, type: TestRoomType, lastName: string): Promise<void> {
  await page.goto('/reservations/new')
  await expect(page.getByRole('heading', { name: 'Dates and guests' })).toBeVisible()
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByRole('radio', { name: new RegExp(type.name) }).check()
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByRole('radio', { name: 'New guest' }).check()
  await page.getByLabel('First name').fill('E2E')
  await page.getByLabel('Last name').fill(lastName)
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByRole('heading', { name: 'Details' })).toBeVisible()
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByRole('heading', { name: 'Review and confirm' })).toBeVisible()
}
