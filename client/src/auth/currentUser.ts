import { createContext, use } from 'react'

import type { User } from '../api/types'

/** The authenticated user, provided by ProtectedRoute to everything it renders. */
export const CurrentUserContext = createContext<User | null>(null)

/** The logged-in user. Only valid inside ProtectedRoute, where it is always present. */
export function useAuthenticatedUser(): User {
  const user = use(CurrentUserContext)
  if (user === null) {
    throw new Error('useAuthenticatedUser must be used inside <ProtectedRoute>')
  }
  return user
}

/** Where to go after login: the page the user was sent away from, if it is a local path. */
export function redirectTarget(state: unknown): string {
  if (typeof state === 'object' && state !== null && 'from' in state) {
    const { from } = state
    if (typeof from === 'string' && from.startsWith('/') && !from.startsWith('//')) {
      return from
    }
  }
  return '/'
}
