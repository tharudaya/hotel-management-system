import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import type {
  DashboardRoom,
  DashboardRooms,
  DashboardSummary,
  HotelInfo,
  HousekeepingStatus,
  ReservationDetail,
} from '../api/types'

export const dashboardKeys = {
  all: ['dashboard'] as const,
  rooms: ['dashboard', 'rooms'] as const,
  summary: ['dashboard', 'summary'] as const,
}

export const reservationKeys = {
  all: ['reservations'] as const,
  detail: (id: number) => ['reservations', id] as const,
}

export const hotelKeys = {
  info: ['hotel'] as const,
}

export function useRooms() {
  return useQuery({
    queryKey: dashboardKeys.rooms,
    queryFn: ({ signal }) => apiFetch<DashboardRooms>('/api/dashboard/rooms', { signal }),
  })
}

export function useSummary() {
  return useQuery({
    queryKey: dashboardKeys.summary,
    queryFn: ({ signal }) => apiFetch<DashboardSummary>('/api/dashboard/summary', { signal }),
  })
}

export function useHotelInfo() {
  return useQuery({
    queryKey: hotelKeys.info,
    queryFn: ({ signal }) => apiFetch<HotelInfo>('/api/hotel', { signal }),
    staleTime: 5 * 60 * 1000,
  })
}

export function useReservation(id: number | null) {
  return useQuery({
    queryKey: reservationKeys.detail(id ?? 0),
    queryFn: ({ signal }) =>
      apiFetch<ReservationDetail>(`/api/reservations/${id ?? 0}`, { signal }),
    enabled: id !== null,
  })
}

export type RoomAction =
  | { kind: 'check-in'; reservationId: number; roomId: number; roomVersion: number }
  | { kind: 'check-out'; reservationId: number; roomVersion: number }
  | { kind: 'housekeeping'; roomId: number; status: HousekeepingStatus; version: number }

function runAction(action: RoomAction): Promise<DashboardRoom> {
  switch (action.kind) {
    case 'check-in':
      return apiFetch<DashboardRoom>(`/api/reservations/${action.reservationId}/check-in`, {
        method: 'POST',
        body: { room_id: action.roomId, room_version: action.roomVersion },
      })
    case 'check-out':
      return apiFetch<DashboardRoom>(`/api/reservations/${action.reservationId}/check-out`, {
        method: 'POST',
        body: { room_version: action.roomVersion },
      })
    case 'housekeeping':
      return apiFetch<DashboardRoom>(`/api/rooms/${action.roomId}/housekeeping-status`, {
        method: 'PATCH',
        body: { status: action.status, version: action.version },
      })
  }
}

/**
 * Run a room action. On success the returned room entry replaces the cached one at
 * once; either way the dashboard and reservation queries are refetched, so after a
 * conflict (409) the user sees the current state.
 */
export function useRoomAction() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: runAction,
    onSuccess: (room) => {
      queryClient.setQueryData<DashboardRooms>(dashboardKeys.rooms, (current) =>
        current
          ? {
              ...current,
              rooms: current.rooms.map((r) => (r.room_id === room.room_id ? room : r)),
            }
          : current,
      )
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: dashboardKeys.all }),
        queryClient.invalidateQueries({ queryKey: reservationKeys.all }),
      ])
    },
  })
}
