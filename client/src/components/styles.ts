/** Shared Tailwind class strings, so focus rings and buttons look the same everywhere. */

export const focusRing =
  'focus-visible:ring-2 focus-visible:ring-sky-600 focus-visible:ring-offset-2 focus-visible:outline-none'

export const buttonPrimary =
  'inline-flex items-center justify-center gap-2 rounded-md bg-sky-700 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-sky-800 disabled:cursor-not-allowed disabled:opacity-60 ' +
  focusRing

export const buttonSecondary =
  'inline-flex items-center justify-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 ' +
  focusRing

export const fieldClass =
  'block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm focus-visible:border-sky-600 focus-visible:ring-2 focus-visible:ring-sky-600/40 focus-visible:outline-none'
