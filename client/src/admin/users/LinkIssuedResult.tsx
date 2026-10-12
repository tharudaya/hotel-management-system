import { useId, useState } from 'react'

import type { LinkIssued } from '../../api/types'
import { useAuthenticatedUser } from '../../auth/currentUser'
import { buttonSecondary, fieldClass } from '../../components/styles'
import { formatDateTime } from '../../lib/format'

/** Result of sending an invite or reset: where it was emailed, or the one-time link. */
export function LinkIssuedResult({
  result,
  what,
}: {
  result: LinkIssued
  /** "invite" or "password reset link" */
  what: string
}) {
  const { property } = useAuthenticatedUser()
  const [copied, setCopied] = useState(false)
  const linkId = useId()
  const expires = formatDateTime(result.expires_at, property.time_zone)

  if (result.delivery === 'email' || !result.link) {
    return (
      <p className="text-sm text-slate-700">
        The {what} was emailed to {result.sent_to ?? 'the user'}. It works once and expires{' '}
        {expires}.
      </p>
    )
  }
  const { link } = result
  return (
    <div className="space-y-2">
      <label htmlFor={linkId} className="block text-sm font-medium text-slate-700">
        Activation link
      </label>
      <div className="flex gap-2">
        <input
          id={linkId}
          readOnly
          value={link}
          onFocus={(e) => {
            e.target.select()
          }}
          className={`${fieldClass} font-mono text-xs`}
        />
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard.writeText(link).then(() => {
              setCopied(true)
            })
          }}
          className={buttonSecondary}
        >
          Copy
        </button>
      </div>
      <p role="status" className="text-sm text-slate-600">
        {copied ? 'Link copied. ' : ''}
        Give this link to the user in person. It works once, expires {expires}, and is shown only
        now.
      </p>
    </div>
  )
}
