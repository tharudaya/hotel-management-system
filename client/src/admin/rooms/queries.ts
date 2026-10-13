import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../../api/client'
import type {
  RoomCreate,
  RoomOut,
  RoomTypeCreate,
  RoomTypeOut,
  RoomTypeUpdate,
  RoomUpdate,
} from '../../api/types'
import { dashboardKeys } from '../../dashboard/queries'
import { type RoomFilters, toApiQuery } from './params'

export const roomSetupKeys = {
  all: ['admin', 'room-setup'] as const,
  types: (includeRetired: boolean) => ['admin', 'room-setup', 'types', includeRetired] as const,
  rooms: (filters: RoomFilters) => ['admin', 'room-setup', 'rooms', filters] as const,
}

export function useRoomTypes(includeRetired: boolean) {
  return useQuery({
    queryKey: roomSetupKeys.types(includeRetired),
    queryFn: ({ signal }) =>
      apiFetch<RoomTypeOut[]>(
        `/api/admin/room-types${includeRetired ? '?include_retired=true' : ''}`,
        { signal },
      ),
  })
}

export function useRooms(filters: RoomFilters) {
  return useQuery({
    queryKey: roomSetupKeys.rooms(filters),
    queryFn: ({ signal }) =>
      apiFetch<RoomOut[]>(`/api/admin/rooms?${toApiQuery(filters)}`, { signal }),
  })
}

/** "Someone else changed this room" (stale version): the caller refetches and says so. */
export function isConflict(error: Error): boolean {
  return error instanceof ApiError && error.code === 'version_conflict'
}

/**
 * Every room setup change refreshes all room setup lists and the dashboard (rooms may
 * appear, vanish or change type). Errors, including 403, are shown where they happen.
 */
function useRoomSetupMutation<TBody, TResult>(request: (body: TBody) => Promise<TResult>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: request,
    meta: { handlesForbidden: true },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: roomSetupKeys.all })
      void queryClient.invalidateQueries({ queryKey: dashboardKeys.all })
    },
  })
}

export function useCreateRoomType() {
  return useRoomSetupMutation((body: RoomTypeCreate) =>
    apiFetch<RoomTypeOut>('/api/admin/room-types', { method: 'POST', body }),
  )
}

export function useUpdateRoomType(id: number) {
  return useRoomSetupMutation((body: RoomTypeUpdate) =>
    apiFetch<RoomTypeOut>(`/api/admin/room-types/${id}`, { method: 'PATCH', body }),
  )
}

export function useRoomTypeStatus(id: number, action: 'retire' | 'reactivate') {
  return useRoomSetupMutation<undefined, RoomTypeOut>(() =>
    apiFetch<RoomTypeOut>(`/api/admin/room-types/${id}/${action}`, { method: 'POST' }),
  )
}

export function useCreateRoom() {
  return useRoomSetupMutation((body: RoomCreate) =>
    apiFetch<RoomOut>('/api/admin/rooms', { method: 'POST', body }),
  )
}

export function useUpdateRoom(id: number) {
  return useRoomSetupMutation((body: RoomUpdate) =>
    apiFetch<RoomOut>(`/api/admin/rooms/${id}`, { method: 'PATCH', body }),
  )
}

export function useRoomStatus(id: number, action: 'retire' | 'reactivate') {
  return useRoomSetupMutation((version: number) =>
    apiFetch<RoomOut>(`/api/admin/rooms/${id}/${action}`, {
      method: 'POST',
      body: { version },
    }),
  )
}
