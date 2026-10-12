import { useInfiniteQuery } from '@tanstack/react-query'

import { apiFetch } from '../../api/client'
import type { AuditPage } from '../../api/types'
import { auditKeys } from '../users/queries'
import { type AuditFilters, toApiQuery } from './params'

/** Newest first; each "Load older" follows the server's cursor. */
export function useAuditLog(filters: AuditFilters, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: [...auditKeys.all, 'log', filters] as const,
    queryFn: ({ pageParam, signal }) => {
      const cursor = pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ''
      return apiFetch<AuditPage>(`/api/admin/audit?${toApiQuery(filters)}${cursor}`, { signal })
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled,
  })
}
