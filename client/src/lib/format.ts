const dateFormat = new Intl.DateTimeFormat('en-GB', {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
})

/**
 * Format a plain ISO date ("2026-10-12") such as a business date.
 * Parsed as UTC and printed in UTC so the viewer's time zone cannot shift the day.
 */
export function formatDate(isoDate: string): string {
  return dateFormat.format(new Date(`${isoDate}T00:00:00Z`))
}

export function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : pluralForm}`
}

const dateTimeFormats = new Map<string, Intl.DateTimeFormat>()

/**
 * Format an instant ("2026-10-12T14:05:00Z") as a date and time in the hotel's time
 * zone, so everyone sees hotel time whatever their browser's zone.
 */
export function formatDateTime(iso: string, timeZone: string): string {
  let format = dateTimeFormats.get(timeZone)
  if (!format) {
    format = new Intl.DateTimeFormat('en-GB', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      timeZone,
    })
    dateTimeFormats.set(timeZone, format)
  }
  return format.format(new Date(iso))
}

const moneyFormats = new Map<string, Intl.NumberFormat>()

/**
 * Format an amount in minor units (öre, cents) of `currency`: 129900, "SEK" ->
 * "SEK 1,299.00". Amounts stay integers everywhere else; dividing happens only here,
 * for display.
 */
export function formatMoney(minor: number, currency: string): string {
  let format = moneyFormats.get(currency)
  if (!format) {
    format = new Intl.NumberFormat('en-GB', { style: 'currency', currency })
    moneyFormats.set(currency, format)
  }
  const digits = format.resolvedOptions().maximumFractionDigits ?? 2
  return format.format(minor / 10 ** digits)
}

/** "1299" or "1299.50" in major units -> minor units, without float arithmetic. */
export function parseAmount(text: string): number | null {
  const match = /^\s*(\d{1,9})(?:[.,](\d{1,2}))?\s*$/.exec(text)
  if (!match) return null
  const [, whole = '0', fraction = ''] = match
  return Number(whole) * 100 + Number(fraction.padEnd(2, '0'))
}

/** Minor units as an editable amount: 129950 -> "1299.50" (integers only, no floats). */
export function amountInput(minor: number): string {
  const whole = Math.floor(minor / 100)
  const cents = minor % 100
  return cents === 0 ? String(whole) : `${whole}.${String(cents).padStart(2, '0')}`
}
