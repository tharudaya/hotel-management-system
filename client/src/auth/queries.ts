import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import type { LoginRequest, User } from '../api/types'

export const authKeys = {
  me: ['auth', 'me'] as const,
}

/** The logged-in user. A 401 here triggers the redirect to /login. */
export function useCurrentUser() {
  return useQuery({
    queryKey: authKeys.me,
    queryFn: ({ signal }) => apiFetch<User>('/api/auth/me', { signal }),
    retry: false,
    staleTime: 5 * 60 * 1000,
  })
}

export function useLogin() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (credentials: LoginRequest) =>
      apiFetch<User>('/api/auth/login', {
        method: 'POST',
        body: credentials,
        // A 401 here means wrong credentials, shown on the form.
        redirectOnUnauthorized: false,
      }),
    onSuccess: (user) => {
      // Drop anything cached for a previous user before storing the new one.
      queryClient.clear()
      queryClient.setQueryData(authKeys.me, user)
    },
  })
}

export function useLogout() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () =>
      apiFetch<undefined>('/api/auth/logout', { method: 'POST', redirectOnUnauthorized: false }),
    onSettled: () => {
      queryClient.clear()
    },
  })
}
