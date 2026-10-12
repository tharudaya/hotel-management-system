import { type QueryClient, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useSyncExternalStore } from 'react'

import { notifyUnauthorized } from '../api/client'
import { authKeys } from '../auth/queries'
import { dashboardKeys, reservationKeys } from '../dashboard/queries'
import { applyEvents } from './cache'
import {
  type ConnectionStatus,
  RealtimeConnection,
  type RealtimeConnectionOptions,
} from './connection'
import type { RealtimeEvent } from './protocol'

/** Same origin as the page; the Vite dev server proxies /ws to the backend. */
export function realtimeUrl(location: Location = window.location): string {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${location.host}/ws`
}

/** A connection whose events patch the TanStack Query cache. Not started yet. */
export function createCacheConnection(
  queryClient: QueryClient,
  url: string,
  overrides: Partial<Pick<RealtimeConnectionOptions, 'createSocket'>> = {},
): RealtimeConnection {
  // Events that arrive while a snapshot refetch is in flight; replayed over the fresh
  // snapshot afterwards, because the snapshot may have been read before them.
  let duringResync: RealtimeEvent[] | null = null

  function resync() {
    duringResync = []
    void queryClient.invalidateQueries({ queryKey: dashboardKeys.all }).finally(() => {
      const replay = duringResync ?? []
      duringResync = null
      if (replay.length > 0) applyEvents(queryClient, replay, { strict: true })
    })
  }

  return new RealtimeConnection({
    url,
    onEvents: (events) => {
      duringResync?.push(...events)
      const { needsResync } = applyEvents(queryClient, events)
      // The open detail panel's reservation may have changed too (cheap: only active
      // queries refetch).
      void queryClient.invalidateQueries({ queryKey: reservationKeys.all })
      if (needsResync) resync()
    },
    onResync: resync,
    onUnauthorized: notifyUnauthorized,
    onPermissionsChanged: () => {
      // New permissions change what the UI offers (actions, guest contact details):
      // refetch the user, then everything permission-dependent.
      void queryClient.invalidateQueries({ queryKey: authKeys.me })
      void queryClient.invalidateQueries({ queryKey: reservationKeys.all })
      resync()
    },
    ...overrides,
  })
}

const noStore = {
  subscribe: () => () => undefined,
  getStatus: (): null => null,
}

/**
 * Keep the dashboard caches live while signed in and return the connection status for
 * the header indicator. Mount once (AppLayout). `enabled` is false for roles without
 * dashboard access (the server would refuse them anyway).
 */
export function useRealtime(enabled: boolean): ConnectionStatus | null {
  const queryClient = useQueryClient()
  // Constructing does nothing; the effect starts and stops it.
  const connection = useMemo(
    () => (enabled ? createCacheConnection(queryClient, realtimeUrl()) : null),
    [enabled, queryClient],
  )

  useEffect(() => {
    if (!connection) return
    connection.start()
    return () => {
      connection.stop()
    }
  }, [connection])

  const store = connection ?? noStore
  return useSyncExternalStore(store.subscribe, store.getStatus)
}
