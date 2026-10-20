import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import type {
  Availability,
  GuestPage,
  ReservationCreate,
  ReservationDetail,
  ReservationUpdate,
  ReservationView,
  WalkInCreate,
} from '../api/types'
import { dashboardKeys, reservationKeys } from '../dashboard/queries'

export interface AvailabilityRequest {
  checkIn: string
  checkOut: string
  adults: number
  children: number
  excludeReservationId?: number | undefined
}

export const bookingKeys = {
  availability: (request: AvailabilityRequest) =>
    ['reservations', 'availability', request] as const,
  guests: (q: string) => ['guests', 'search', q] as const,
  preview: (id: number, body: ReservationUpdate) => ['reservations', id, 'preview', body] as const,
}

// 4xx answers are final (validation, conflicts): do not retry them.
function retryServerErrors(count: number, error: Error): boolean {
  return !(error instanceof ApiError && error.status < 500) && count < 2
}

/** Room types for the stay. `null` while the stay is not valid yet. */
export function useAvailability(request: AvailabilityRequest | null) {
  return useQuery({
    queryKey: bookingKeys.availability(
      request ?? { checkIn: '', checkOut: '', adults: 0, children: 0 },
    ),
    queryFn: ({ signal }) => {
      if (!request) throw new Error('No stay to check')
      const r = request
      const query = new URLSearchParams({
        check_in: r.checkIn,
        check_out: r.checkOut,
        adults: String(r.adults),
        children: String(r.children),
      })
      if (r.excludeReservationId) {
        query.set('exclude_reservation_id', String(r.excludeReservationId))
      }
      return apiFetch<Availability>(`/api/reservations/availability?${query.toString()}`, {
        signal,
      })
    },
    enabled: request !== null,
    // Availability changes all the time: never trust a cached count for long.
    staleTime: 0,
    retry: retryServerErrors,
    meta: { handlesForbidden: true },
  })
}

export function useGuestSearch(q: string, enabled = true) {
  return useQuery({
    queryKey: bookingKeys.guests(q),
    queryFn: ({ signal }) =>
      apiFetch<GuestPage>(`/api/guests?${new URLSearchParams({ q, page_size: '8' }).toString()}`, {
        signal,
      }),
    enabled: enabled && q.trim().length >= 2,
    retry: retryServerErrors,
    meta: { handlesForbidden: true },
  })
}

function useAfterBooking() {
  const queryClient = useQueryClient()
  // The new booking is on the lists, the dashboard and (for walk-ins) a room tile.
  return async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['reservations'] }),
      queryClient.invalidateQueries({ queryKey: dashboardKeys.all }),
    ])
  }
}

/** POST /api/reservations with the attempt's key: a double click or a retry after a lost
 * answer gets the same booking back. */
export function useCreateReservation(idempotencyKey: string) {
  const after = useAfterBooking()
  return useMutation({
    mutationFn: (body: ReservationCreate) =>
      apiFetch<ReservationDetail>('/api/reservations', {
        method: 'POST',
        body,
        headers: { 'Idempotency-Key': idempotencyKey },
      }),
    meta: { handlesForbidden: true },
    onSuccess: after,
  })
}

export function useWalkIn(idempotencyKey: string) {
  const after = useAfterBooking()
  return useMutation({
    mutationFn: (body: WalkInCreate) =>
      apiFetch<ReservationDetail>('/api/reservations/walk-in', {
        method: 'POST',
        body,
        headers: { 'Idempotency-Key': idempotencyKey },
      }),
    meta: { handlesForbidden: true },
    onSuccess: after,
  })
}

/** What saving `body` would make of the booking (nothing is saved). */
export function usePreviewChange(id: number, body: ReservationUpdate | null) {
  return useQuery({
    queryKey: bookingKeys.preview(id, body ?? { version: 0 }),
    queryFn: ({ signal }) =>
      apiFetch<ReservationView>(`/api/reservations/${id}/preview-change`, {
        method: 'POST',
        body,
        signal,
      }),
    enabled: body !== null,
    staleTime: 0,
    retry: retryServerErrors,
    meta: { handlesForbidden: true },
  })
}

export function useSaveChange(id: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ReservationUpdate) =>
      apiFetch<ReservationView>(`/api/reservations/${id}`, { method: 'PATCH', body }),
    meta: { handlesForbidden: true },
    onSuccess: (view) => {
      queryClient.setQueryData(reservationKeys.detail(id), view)
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: reservationKeys.detail(id) })
      }
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['reservations', 'list'] }),
        queryClient.invalidateQueries({ queryKey: dashboardKeys.all }),
      ])
    },
  })
}
