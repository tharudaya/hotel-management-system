import type { ReactNode } from 'react'

import { focusRing } from './styles'

/** Inputs on the signed-out pages (login, activation, password reset). */
export const authInputClass =
  'block w-full rounded-md border border-slate-300 bg-white px-3 py-2.5 text-base text-slate-900 ' +
  'shadow-sm placeholder:text-slate-400 ' +
  'focus-visible:border-sky-600 focus-visible:ring-2 focus-visible:ring-sky-600/40 focus-visible:outline-none ' +
  'aria-invalid:border-red-600 disabled:bg-slate-100'

export const authButtonClass =
  'flex w-full items-center justify-center rounded-md bg-sky-700 px-4 py-2.5 text-base font-medium text-white shadow-sm hover:bg-sky-800 disabled:cursor-not-allowed disabled:opacity-60 ' +
  focusRing

export const authLinkClass =
  'rounded-sm font-medium text-sky-700 underline-offset-2 hover:text-sky-800 hover:underline ' +
  focusRing

export const authCardClass = 'rounded-xl bg-white p-6 shadow-md ring-1 ring-slate-200 sm:p-8'

/** Error box that is always mounted, so screen readers announce changes. */
export function FormAlert({ id, message }: { id?: string; message: string | null }) {
  return (
    <div
      id={id}
      role="alert"
      className={
        message
          ? 'rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800'
          : 'sr-only'
      }
    >
      {message}
    </div>
  )
}

/** Centered layout shared by the signed-out pages: brand line, page heading, card. */
export function AuthShell({
  title,
  children,
  footer,
}: {
  title: string
  children: ReactNode
  footer?: ReactNode
}) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-100 px-4 py-12 sm:px-6">
      <div className="w-full max-w-md">
        <div className="mb-6 text-center">
          <p className="text-sm font-semibold tracking-wide text-sky-700 uppercase">
            Hotel Management
          </p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-900">{title}</h1>
        </div>
        {children}
        {footer && <div className="mt-6 text-center text-sm text-slate-600">{footer}</div>}
      </div>
    </main>
  )
}
