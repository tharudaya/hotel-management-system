import type { PermissionGroup } from '../../api/types'

const GROUP_LABELS: Record<string, string> = {
  users: 'Users',
  roles: 'Roles',
  dashboard: 'Dashboard',
  rooms: 'Rooms',
  reservations: 'Reservations',
  guests: 'Guests',
  payments: 'Payments',
  reports: 'Reports',
  audit: 'Audit log',
  settings: 'Settings',
}

export function groupLabel(group: string): string {
  return GROUP_LABELS[group] ?? group.charAt(0).toUpperCase() + group.slice(1)
}

/** key -> description, for summaries. */
export function describePermissions(groups: readonly PermissionGroup[]): Map<string, string> {
  return new Map(groups.flatMap((g) => g.permissions.map((p) => [p.key, p.description] as const)))
}

/** Groups narrowed to permissions matching the filter text (key, description or area). */
export function filterCatalog(groups: readonly PermissionGroup[], text: string): PermissionGroup[] {
  const needle = text.trim().toLowerCase()
  if (!needle) return [...groups]
  return groups
    .map((g) => {
      const groupMatches = groupLabel(g.group).toLowerCase().includes(needle)
      return {
        group: g.group,
        permissions: groupMatches
          ? g.permissions
          : g.permissions.filter(
              (p) =>
                p.key.toLowerCase().includes(needle) ||
                p.description.toLowerCase().includes(needle),
            ),
      }
    })
    .filter((g) => g.permissions.length > 0)
}
