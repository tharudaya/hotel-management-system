import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../../api/client'
import type {
  PermissionGroup,
  RoleCreate,
  RoleDetail,
  RoleSummary,
  RoleUpdate,
} from '../../api/types'
import { authKeys } from '../../auth/queries'
import { userKeys } from '../users/queries'

export const roleKeys = {
  all: ['admin', 'roles'] as const,
  list: ['admin', 'roles', 'list'] as const,
  catalog: ['admin', 'roles', 'catalog'] as const,
  detail: (id: number) => ['admin', 'roles', 'detail', id] as const,
}

export function useRoles() {
  return useQuery({
    queryKey: roleKeys.list,
    queryFn: ({ signal }) => apiFetch<RoleSummary[]>('/api/admin/roles', { signal }),
  })
}

export function usePermissionCatalog() {
  return useQuery({
    queryKey: roleKeys.catalog,
    queryFn: ({ signal }) =>
      apiFetch<PermissionGroup[]>('/api/admin/roles/permissions', { signal }),
    // The catalog only changes with a deploy.
    staleTime: Infinity,
  })
}

export function useRole(id: number) {
  return useQuery({
    queryKey: roleKeys.detail(id),
    queryFn: ({ signal }) => apiFetch<RoleDetail>(`/api/admin/roles/${id}`, { signal }),
  })
}

/** Role changes affect role lists, assignable roles and possibly your own access. */
function useRefreshRoles() {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: roleKeys.list })
    void queryClient.invalidateQueries({ queryKey: userKeys.roles })
    void queryClient.invalidateQueries({ queryKey: authKeys.me })
  }
}

// Errors (including 403) are shown in the form or dialog.
const INLINE_ERRORS = { handlesForbidden: true } as const

export function useCreateRole() {
  const queryClient = useQueryClient()
  const refresh = useRefreshRoles()
  return useMutation({
    mutationFn: (body: RoleCreate) =>
      apiFetch<RoleDetail>('/api/admin/roles', { method: 'POST', body }),
    meta: INLINE_ERRORS,
    onSuccess: (role) => {
      queryClient.setQueryData(roleKeys.detail(role.id), role)
      refresh()
    },
  })
}

export function useUpdateRole(id: number) {
  const queryClient = useQueryClient()
  const refresh = useRefreshRoles()
  return useMutation({
    mutationFn: (body: RoleUpdate) =>
      apiFetch<RoleDetail>(`/api/admin/roles/${id}`, { method: 'PATCH', body }),
    meta: INLINE_ERRORS,
    onSuccess: (role) => {
      queryClient.setQueryData(roleKeys.detail(id), role)
      refresh()
    },
  })
}

export function useDeleteRole(id: number) {
  const queryClient = useQueryClient()
  const refresh = useRefreshRoles()
  return useMutation({
    mutationFn: () => apiFetch<undefined>(`/api/admin/roles/${id}`, { method: 'DELETE' }),
    meta: INLINE_ERRORS,
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: roleKeys.detail(id) })
      refresh()
    },
  })
}
