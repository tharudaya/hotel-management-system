import { useEffect } from 'react'

export const APP_NAME = 'Hotel Management'

/** Set the browser tab title ("Users – Hotel Management"); screen readers read it. */
export function useDocumentTitle(title: string | null | undefined): void {
  useEffect(() => {
    document.title = title ? `${title} – ${APP_NAME}` : APP_NAME
  }, [title])
}
