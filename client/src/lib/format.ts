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
