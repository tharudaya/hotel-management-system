import { useCallback, useMemo } from 'react'

import { can as userCan, canAny as userCanAny, type PermissionKey } from './permissions'
import { useCurrentUser } from './queries'

export interface Permissions {
  can: (permission: PermissionKey) => boolean
  canAny: (...permissions: PermissionKey[]) => boolean
  /** True until /api/auth/me has answered; everything is denied meanwhile. */
  isLoading: boolean
}

/**
 * The signed-in user's permissions, from the /api/auth/me query. Updates as soon as
 * /me is refetched (after a 403, or when the server says permissions changed).
 */
export function usePermissions(): Permissions {
  const { data: user, isPending } = useCurrentUser()
  const can = useCallback((permission: PermissionKey) => userCan(user, permission), [user])
  const canAny = useCallback(
    (...permissions: PermissionKey[]) => userCanAny(user, ...permissions),
    [user],
  )
  return useMemo(() => ({ can, canAny, isLoading: isPending }), [can, canAny, isPending])
}
