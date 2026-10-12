/** Shared rules and helpers for the user forms (create and edit profile). */
import { ApiError } from '../../api/client'

export const USERNAME_PATTERN = /^[A-Za-z0-9._-]{3,50}$/
export const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

export type FieldErrors<F extends string> = Partial<Record<F, string>>

/** "" -> null, otherwise trimmed: empty optional fields clear the value. */
export function optional(value: string): string | null {
  const trimmed = value.trim()
  return trimmed === '' ? null : trimmed
}

/** A date input ("2026-12-31") as the end of that day in the browser's time zone. */
export function endOfDay(date: string): string | null {
  return date ? new Date(`${date}T23:59:59`).toISOString() : null
}

/** An instant as a date input value in the browser's time zone. */
export function toDateInput(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function validateEmail(email: string): string | undefined {
  const value = email.trim()
  return value && !EMAIL_PATTERN.test(value)
    ? 'Enter a valid email address, like name@example.com.'
    : undefined
}

/** Server conflicts that belong to one field. */
export function fieldForError(error: Error): 'username' | 'email' | null {
  if (!(error instanceof ApiError)) return null
  if (error.code === 'username_taken') return 'username'
  if (error.code === 'email_taken') return 'email'
  return null
}

export function messageFor(error: Error): string {
  if (error instanceof ApiError && error.status < 500) return error.message
  return 'Something went wrong. Please try again.'
}
