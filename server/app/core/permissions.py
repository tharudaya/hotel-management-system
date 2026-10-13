"""The permission catalog: every permission the application knows about.

This module is the single source of truth. On startup the catalog is synced to the
`permissions` table (services/permissions.sync_permission_catalog): new keys are
inserted, changed descriptions/groups updated, and keys no longer listed here are
deleted together with their role_permissions rows.

Check permissions by key (e.g. require_permission(P.RESERVATION_CHECK_IN)), never by
role name: roles are editable bundles of permissions stored in the database.
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Permission:
    key: str
    description: str
    group: str


class P:
    """Permission keys as constants, so typos fail at import time, not at runtime."""

    USER_VIEW: Final = "user.view"
    USER_MANAGE: Final = "user.manage"
    ROLE_VIEW: Final = "role.view"
    ROLE_MANAGE: Final = "role.manage"
    DASHBOARD_VIEW: Final = "dashboard.view"
    ROOM_UPDATE_HOUSEKEEPING_STATUS: Final = "room.update_housekeeping_status"
    ROOM_SET_OUT_OF_ORDER: Final = "room.set_out_of_order"
    ROOM_MANAGE: Final = "room.manage"
    RESERVATION_VIEW: Final = "reservation.view"
    RESERVATION_CREATE: Final = "reservation.create"
    RESERVATION_MODIFY: Final = "reservation.modify"
    RESERVATION_CANCEL: Final = "reservation.cancel"
    RESERVATION_CHECK_IN: Final = "reservation.check_in"
    RESERVATION_CHECK_OUT: Final = "reservation.check_out"
    GUEST_VIEW: Final = "guest.view"
    GUEST_VIEW_CONTACT: Final = "guest.view_contact"
    PAYMENT_VIEW: Final = "payment.view"
    PAYMENT_REFUND: Final = "payment.refund"
    REPORT_VIEW: Final = "report.view"
    AUDIT_VIEW: Final = "audit.view"
    SETTINGS_MANAGE: Final = "settings.manage"


PERMISSIONS: Final[tuple[Permission, ...]] = (
    Permission(P.USER_VIEW, "View staff accounts", "users"),
    Permission(P.USER_MANAGE, "Create, edit and deactivate staff accounts", "users"),
    Permission(P.ROLE_VIEW, "View roles and their permissions", "roles"),
    Permission(P.ROLE_MANAGE, "Create and edit roles and assign them", "roles"),
    Permission(P.DASHBOARD_VIEW, "View the front desk dashboard", "dashboard"),
    Permission(P.ROOM_UPDATE_HOUSEKEEPING_STATUS, "Change a room's housekeeping status", "rooms"),
    Permission(P.ROOM_SET_OUT_OF_ORDER, "Take rooms out of order or back into service", "rooms"),
    Permission(P.ROOM_MANAGE, "Add, edit and retire rooms and room types", "rooms"),
    Permission(P.RESERVATION_VIEW, "View reservations", "reservations"),
    Permission(P.RESERVATION_CREATE, "Create reservations", "reservations"),
    Permission(P.RESERVATION_MODIFY, "Change reservations", "reservations"),
    Permission(P.RESERVATION_CANCEL, "Cancel reservations", "reservations"),
    Permission(P.RESERVATION_CHECK_IN, "Check guests in", "reservations"),
    Permission(P.RESERVATION_CHECK_OUT, "Check guests out", "reservations"),
    Permission(P.GUEST_VIEW, "View guest profiles", "guests"),
    Permission(P.GUEST_VIEW_CONTACT, "View guest email addresses and phone numbers", "guests"),
    Permission(P.PAYMENT_VIEW, "View payments", "payments"),
    Permission(P.PAYMENT_REFUND, "Issue refunds", "payments"),
    Permission(P.REPORT_VIEW, "View reports", "reports"),
    Permission(P.AUDIT_VIEW, "View the audit log", "audit"),
    Permission(P.SETTINGS_MANAGE, "Change property settings", "settings"),
)

ALL_PERMISSION_KEYS: Final[frozenset[str]] = frozenset(p.key for p in PERMISSIONS)


@dataclass(frozen=True)
class DefaultRole:
    name: str
    description: str
    permissions: frozenset[str]


ADMIN_ROLE_NAME: Final = "Admin"


def _in_groups(*groups: str) -> frozenset[str]:
    return frozenset(p.key for p in PERMISSIONS if p.group in groups)


# System roles created for every property (is_system = true; cannot be deleted).
# Admin always has every permission, including keys added later, and cannot be edited.
# Room setup (room.manage) is Admin-only: roles defined by formula exclude it explicitly.
ADMIN_ONLY: Final[frozenset[str]] = frozenset({P.SETTINGS_MANAGE, P.ROOM_MANAGE})
DEFAULT_ROLES: Final[tuple[DefaultRole, ...]] = (
    DefaultRole(ADMIN_ROLE_NAME, "Full access, including settings", ALL_PERMISSION_KEYS),
    DefaultRole(
        "General manager",
        "Everything except property settings and room setup",
        ALL_PERMISSION_KEYS - ADMIN_ONLY,
    ),
    DefaultRole(
        "Front office manager",
        "Runs the front desk: rooms, reservations, guests, payments and reports",
        (
            _in_groups("dashboard", "rooms", "reservations", "guests", "payments")
            | {P.REPORT_VIEW, P.USER_VIEW}
        )
        - ADMIN_ONLY,
    ),
    DefaultRole(
        "Receptionist",
        "Day-to-day front desk work",
        frozenset(
            {
                P.DASHBOARD_VIEW,
                P.RESERVATION_VIEW,
                P.RESERVATION_CREATE,
                P.RESERVATION_MODIFY,
                P.RESERVATION_CHECK_IN,
                P.RESERVATION_CHECK_OUT,
                P.GUEST_VIEW,
                P.GUEST_VIEW_CONTACT,
                P.PAYMENT_VIEW,
                P.ROOM_UPDATE_HOUSEKEEPING_STATUS,
            }
        ),
    ),
    DefaultRole(
        "Housekeeping supervisor",
        "Oversees housekeeping and room availability",
        frozenset({P.DASHBOARD_VIEW, P.ROOM_UPDATE_HOUSEKEEPING_STATUS, P.ROOM_SET_OUT_OF_ORDER}),
    ),
    DefaultRole(
        "Room attendant",
        "Updates the housekeeping status of rooms",
        frozenset({P.ROOM_UPDATE_HOUSEKEEPING_STATUS}),
    ),
)
