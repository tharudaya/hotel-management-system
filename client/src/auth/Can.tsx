import type { ReactNode } from 'react'

import type { PermissionKey } from './permissions'
import { usePermissions } from './usePermissions'

type CanProps = {
  children: ReactNode
  /** Rendered instead of the children when not allowed (default: nothing). */
  fallback?: ReactNode
} & (
  | { /** Required permission. */ permission: PermissionKey; anyOf?: never }
  | {
      /** Allowed with at least one of these. */ anyOf: readonly PermissionKey[]
      permission?: never
    }
)

/** Renders its children only when the signed-in user has the permission(s). UI only. */
export function Can({ children, fallback = null, ...rule }: CanProps) {
  const { can, canAny } = usePermissions()
  const allowed = rule.permission !== undefined ? can(rule.permission) : canAny(...rule.anyOf)
  return allowed ? children : fallback
}
