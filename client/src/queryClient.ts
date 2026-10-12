import { QueryClient } from '@tanstack/react-query'

import { ApiError } from './api/client'

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Client errors (4xx) won't succeed on retry; network/5xx errors might.
        retry: (failureCount, error) =>
          !(error instanceof ApiError && error.status < 500) && failureCount < 2,
      },
    },
  })
}
