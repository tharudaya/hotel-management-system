import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../../api/client'
import type {
  AssignableRole,
  AuditPage,
  LinkIssued,
  SessionsRevoked,
  UserCreate,
  UserDetail,
  UserListPage,
  UserUpdate,
} from '../../api/types'
import { authKeys } from '../../auth/queries'
import { type UserListParams, toApiQuery } from './params'

export const userKeys = {
  all: ['admin', 'users'] as const,
  lists: ['admin', 'users', 'list'] as const,
  list: (params: UserListParams) => ['admin', 'users', 'list', params] as const,
  detail: (id: number) => ['admin', 'users', 'detail', id] as const,
  roles: ['admin', 'users', 'roles'] as const,
}

export const auditKeys = {
  all: ['admin', 'audit'] as const,
  forUser: (id: number) => ['admin', 'audit', 'user', id] as const,
}

export const RECENT_AUDIT_ENTRIES = 10

export function useUserList(params: UserListParams) {
  return useQuery({
    queryKey: userKeys.list(params),
    queryFn: ({ signal }) =>
      apiFetch<UserListPage>(`/api/admin/users?${toApiQuery(params)}`, { signal }),
    // Keep the current page on screen while the next one loads (no table flicker).
    placeholderData: keepPreviousData,
  })
}

export function useAssignableRoles() {
  return useQuery({
    queryKey: userKeys.roles,
    queryFn: ({ signal }) => apiFetch<AssignableRole[]>('/api/admin/users/roles', { signal }),
    staleTime: 60_000,
  })
}

export function useUser(id: number) {
  return useQuery({
    queryKey: userKeys.detail(id),
    queryFn: ({ signal }) => apiFetch<UserDetail>(`/api/admin/users/${id}`, { signal }),
  })
}

export function useUserAudit(id: number, enabled: boolean) {
  return useQuery({
    queryKey: auditKeys.forUser(id),
    queryFn: ({ signal }) =>
      apiFetch<AuditPage>(
        `/api/admin/audit?target_type=user&target_id=${id}&limit=${RECENT_AUDIT_ENTRIES}`,
        { signal },
      ),
    enabled,
  })
}

/** After any change to a user: their detail, every list page and their history. */
function useRefreshUser() {
  const queryClient = useQueryClient()
  return (detail: UserDetail | null, id: number) => {
    // The response is the fresh user; refetch only when the action returned something else.
    if (detail) queryClient.setQueryData(userKeys.detail(id), detail)
    else void queryClient.invalidateQueries({ queryKey: userKeys.detail(id) })
    void queryClient.invalidateQueries({ queryKey: userKeys.lists })
    void queryClient.invalidateQueries({ queryKey: auditKeys.forUser(id) })
    // Editing yourself changes what the header shows.
    const me = queryClient.getQueryData<{ id: number }>(authKeys.me)
    if (me?.id === id) void queryClient.invalidateQueries({ queryKey: authKeys.me })
  }
}

// Mutations show errors (including 403) next to the form or in the dialog.
const INLINE_ERRORS = { handlesForbidden: true } as const

export function useCreateUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: UserCreate) =>
      apiFetch<UserDetail>('/api/admin/users', { method: 'POST', body }),
    meta: INLINE_ERRORS,
    onSuccess: (user) => {
      queryClient.setQueryData(userKeys.detail(user.id), user)
      void queryClient.invalidateQueries({ queryKey: userKeys.lists })
    },
  })
}

export function useUpdateUser(id: number) {
  const refresh = useRefreshUser()
  return useMutation({
    mutationFn: (body: UserUpdate) =>
      apiFetch<UserDetail>(`/api/admin/users/${id}`, { method: 'PATCH', body }),
    meta: INLINE_ERRORS,
    onSuccess: (user) => {
      refresh(user, id)
    },
  })
}

export function useAssignRole(id: number) {
  const refresh = useRefreshUser()
  return useMutation({
    mutationFn: (roleId: number) =>
      apiFetch<UserDetail>(`/api/admin/users/${id}/role`, {
        method: 'PUT',
        body: { role_id: roleId },
      }),
    meta: INLINE_ERRORS,
    onSuccess: (user) => {
      refresh(user, id)
    },
  })
}

/** Actions on a user and what each one answers. */
export interface UserActionResults {
  deactivate: UserDetail
  reactivate: UserDetail
  'revoke-sessions': SessionsRevoked
  invite: LinkIssued
  'reset-password': LinkIssued
}
export type UserActionPath = keyof UserActionResults

export function useUserAction<A extends UserActionPath>(id: number, action: A) {
  const refresh = useRefreshUser()
  return useMutation({
    mutationFn: () =>
      apiFetch<UserActionResults[A]>(`/api/admin/users/${id}/${action}`, { method: 'POST' }),
    meta: INLINE_ERRORS,
    onSuccess: (result) => {
      refresh('status' in result && 'restrictions' in result ? result : null, id)
    },
  })
}
