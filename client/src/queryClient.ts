import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query'

import { ApiError } from './api/client'
import { authKeys } from './auth/queries'
import { showToast } from './components/toast'

/** Per-query/mutation options for the global error handling below. */
export interface AppMeta extends Record<string, unknown> {
  /** The caller shows 403s itself (e.g. inline); skip the global toast. */
  handlesForbidden?: boolean
}

declare module '@tanstack/react-query' {
  interface Register {
    queryMeta: AppMeta
    mutationMeta: AppMeta
  }
}

export const FORBIDDEN_MESSAGE =
  'You do not have permission to do that. Your access may have just changed; the page now shows what you can use.'

export function createQueryClient(): QueryClient {
  // A 403 usually means permissions changed since /me was loaded: refetch it so the
  // UI (navigation, buttons) matches what the server allows, and tell the user.
  function onError(error: Error, meta: AppMeta | undefined): void {
    if (!(error instanceof ApiError) || error.status !== 403) return
    void queryClient.invalidateQueries({ queryKey: authKeys.me })
    if (!meta?.handlesForbidden) showToast(FORBIDDEN_MESSAGE)
  }

  const queryClient: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        onError(error, query.meta)
      },
    }),
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        onError(error, mutation.meta)
      },
    }),
    defaultOptions: {
      queries: {
        // Client errors (4xx) won't succeed on retry; network/5xx errors might.
        retry: (failureCount, error) =>
          !(error instanceof ApiError && error.status < 500) && failureCount < 2,
      },
    },
  })
  return queryClient
}
