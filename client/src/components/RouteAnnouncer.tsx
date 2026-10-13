import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router'

const WAIT_FOR_HEADING_MS = 10_000

function focusWasLost(): boolean {
  const active = document.activeElement
  return active === null || active === document.body || !active.isConnected
}

/**
 * Single-page apps change pages silently. After each move to another page (a path
 * change, not filter or query changes), announce the new page's heading and, if focus
 * was lost with the old page, move it there. Pages often load first, so this waits
 * for the heading to appear instead of announcing a placeholder.
 */
export function RouteAnnouncer() {
  const { pathname } = useLocation()
  const [message, setMessage] = useState('')
  const previous = useRef(pathname)

  useEffect(() => {
    if (previous.current === pathname) return
    previous.current = pathname
    const main = document.getElementById('main')
    if (!main) return

    function settle(heading: HTMLElement) {
      setMessage(heading.textContent)
      if (focusWasLost()) {
        heading.tabIndex = -1
        heading.focus()
      }
    }

    const existing = main.querySelector<HTMLElement>('h1')
    if (existing) {
      settle(existing)
      return
    }
    const observer = new MutationObserver(() => {
      const heading = main.querySelector<HTMLElement>('h1')
      if (heading) {
        observer.disconnect()
        settle(heading)
      }
    })
    observer.observe(main, { childList: true, subtree: true })
    const timer = setTimeout(() => {
      observer.disconnect()
    }, WAIT_FOR_HEADING_MS)
    return () => {
      observer.disconnect()
      clearTimeout(timer)
    }
  }, [pathname])

  return (
    <div aria-live="polite" aria-atomic="true" className="sr-only">
      {message}
    </div>
  )
}
