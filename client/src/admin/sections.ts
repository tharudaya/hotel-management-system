import { P, type PermissionKey } from '../auth/permissions'

/** Admin sections, in navigation order, with the permission each one needs. */
export const ADMIN_SECTIONS: readonly { path: string; label: string; permission: PermissionKey }[] =
  [
    { path: 'users', label: 'Users', permission: P.USER_VIEW },
    { path: 'roles', label: 'Roles', permission: P.ROLE_VIEW },
    { path: 'rooms', label: 'Rooms', permission: P.ROOM_MANAGE },
    { path: 'audit', label: 'Audit log', permission: P.AUDIT_VIEW },
  ]
