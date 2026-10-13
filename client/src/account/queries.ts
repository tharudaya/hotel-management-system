import { useMutation, useQuery } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import type {
  ForgotPasswordRequest,
  Message,
  SetPasswordRequest,
  TokenInfo,
  TokenRequest,
} from '../api/types'

/** The two kinds of emailed link: an invite (/activate) or a password reset. */
export type LinkKind = 'activate' | 'reset-password'

export const accountKeys = {
  link: (kind: LinkKind, token: string) => ['account', kind, token] as const,
}

// Signed-out pages: a 401 never means "session lost" here.
const PUBLIC = { redirectOnUnauthorized: false } as const

/**
 * Who the link is for. 404 (unknown) and 410 (used/expired) are answers, not retries.
 * The token travels in the request body, never in a URL (URLs end up in logs).
 */
export function useLinkInfo(kind: LinkKind, token: string) {
  return useQuery({
    queryKey: accountKeys.link(kind, token),
    queryFn: ({ signal }) =>
      apiFetch<TokenInfo>(`/api/auth/${kind}/check`, {
        ...PUBLIC,
        method: 'POST',
        body: { token } satisfies TokenRequest,
        signal,
      }),
    enabled: token !== '',
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })
}

export function useSetPassword(kind: LinkKind, token: string) {
  return useMutation({
    mutationFn: (password: string) =>
      apiFetch<Message>(`/api/auth/${kind}`, {
        ...PUBLIC,
        method: 'POST',
        body: { token, password } satisfies SetPasswordRequest,
      }),
  })
}

export function useForgotPassword() {
  return useMutation({
    mutationFn: (body: ForgotPasswordRequest) =>
      apiFetch<Message>('/api/auth/forgot-password', { ...PUBLIC, method: 'POST', body }),
  })
}
