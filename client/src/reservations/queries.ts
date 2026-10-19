import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import type { ReservationPage, ReservationUpdate, ReservationView } from '../api/types'
import { dashboardKeys, reservationKeys } from '../dashboard/queries'
import { type ReservationListParams, toApiQuery } from './params'

/** Lists sit under reservationKeys.all, so realtime events refresh them too. */
export const reservationListKeys = {
  all: ['reservations', 'list'] as const,
  list: (params: ReservationListParams) => ['reservations', 'list', params] as const,
}

export function useReservationList(params: ReservationListParams) {
  return useQuery({
    queryKey: reservationListKeys.list(params),
    queryFn: ({ signal }) =>
      apiFetch<ReservationPage>(`/api/reservations?${toApiQuery(params)}`, { signal }),
    // Keep the current page on screen while the next one loads (no table flicker).
    placeholderData: keepPreviousData,
  })
}

/** The same cache entry as the dashboard's reservation panel. */
export function useReservationView(id: number) {
  return useQuery({
    queryKey: reservationKeys.detail(id),
    queryFn: ({ signal }) => apiFetch<ReservationView>(`/api/reservations/${id}`, { signal }),
  })
}

/** Changes answered with the fresh reservation. */
export type ReservationChange =
  | { kind: 'modify'; body: ReservationUpdate }
  | { kind: 'cancel'; version: number; reason: string }
  | { kind: 'no-show'; version: number }
  | { kind: 'assign-room'; version: number; roomId: number }
  | { kind: 'unassign-room'; version: number }

function runChange(id: number, change: ReservationChange): Promise<ReservationView> {
  const url = `/api/reservations/${id}`
  switch (change.kind) {
    case 'modify':
      return apiFetch<ReservationView>(url, { method: 'PATCH', body: change.body })
    case 'cancel':
      return apiFetch<ReservationView>(`${url}/cancel`, {
        method: 'POST',
        body: { version: change.version, reason: change.reason },
      })
    case 'no-show':
      return apiFetch<ReservationView>(`${url}/no-show`, {
        method: 'POST',
        body: { version: change.version },
      })
    case 'assign-room':
      return apiFetch<ReservationView>(`${url}/assign-room`, {
        method: 'POST',
        body: { version: change.version, room_id: change.roomId },
      })
    case 'unassign-room':
      return apiFetch<ReservationView>(`${url}/unassign-room`, {
        method: 'POST',
        body: { version: change.version },
      })
  }
}

/**
 * Change a reservation. The answer replaces the cached detail at once; lists and the
 * dashboard are refetched. After a conflict (409) the detail is refetched too, so the
 * page shows what someone else changed.
 */
export function useReservationChange(id: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (change: ReservationChange) => runChange(id, change),
    // Dialogs show errors (including 403) inline.
    meta: { handlesForbidden: true },
    onSuccess: (view) => {
      queryClient.setQueryData(reservationKeys.detail(id), view)
    },
    onError: (error) => {
      if (error instanceof ApiError && (error.status === 409 || error.status === 404)) {
        void queryClient.invalidateQueries({ queryKey: reservationKeys.detail(id) })
      }
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: reservationListKeys.all }),
        queryClient.invalidateQueries({ queryKey: dashboardKeys.all }),
      ])
    },
  })
}

/** Wording for a failed change, written for front desk staff. */
export function changeErrorMessage(error: Error): string {
  if (error instanceof ApiError) {
    if (error.status === 409 && error.code === 'version_conflict') {
      return 'Someone else changed this booking while you were looking at it. The latest version is now shown; check it and try again.'
    }
    if (error.status === 404) return 'This booking no longer exists.'
    if (error.status === 403) return 'You do not have permission to do that.'
    if (error.status < 500) return error.message
  }
  return 'Something went wrong. Please try again.'
}
