import type { UserRole } from '../api/types'

/** Roles the server lets read the dashboard and its live updates (UI convenience only). */
export function canViewDashboard(role: UserRole): boolean {
  return role === 'front_desk' || role === 'manager' || role === 'admin'
}
