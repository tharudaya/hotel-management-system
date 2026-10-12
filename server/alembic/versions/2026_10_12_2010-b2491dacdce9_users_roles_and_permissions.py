"""users, roles and permissions

Replaces the fixed users.role enum with properties, database roles (bundles of
permissions) and per-property role assignments; replaces users.is_active with
deactivated_at.

Upgrade:
  1. create properties, permissions, roles, role_permissions, user_property_roles
  2. add the new user columns (email, phone, job_title, employee_id, expires_at,
     last_login_at, deactivated_at, deactivated_by)
  3. create one property for the existing data (name and time zone from settings)
  4. insert the permission catalog as of this migration
  5. create the default system roles with their permissions
  6. assign every user the role matching their old enum value
  7. inactive users get deactivated_at = now
  8. drop users.role (and the user_role enum type) and users.is_active

Downgrade restores users.role and users.is_active from the new data, then drops the
new tables and columns. It is lossy where the new model is richer than the old one:
roles map back to the closest old value (Front office manager -> manager, Room
attendant -> housekeeping, custom roles and unassigned users -> front_desk), only
assignments at the migration's property are considered, and expiry dates and the new
profile fields are discarded.

The catalog and role definitions are copied here on purpose: a migration must keep
doing the same thing even after app/core/permissions.py changes. Later catalog
changes are applied by the startup sync, not by migrations.

Revision ID: b2491dacdce9
Revises: 947237f34654
Create Date: 2026-10-12 20:10:09.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b2491dacdce9"
down_revision: str | Sequence[str] | None = "947237f34654"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- frozen data (as of this revision) ---------------------------------------------

PERMISSIONS: list[tuple[str, str, str]] = [
    ("user.view", "View staff accounts", "users"),
    ("user.manage", "Create, edit and deactivate staff accounts", "users"),
    ("role.view", "View roles and their permissions", "roles"),
    ("role.manage", "Create and edit roles and assign them", "roles"),
    ("dashboard.view", "View the front desk dashboard", "dashboard"),
    ("room.update_housekeeping_status", "Change a room's housekeeping status", "rooms"),
    ("room.set_out_of_order", "Take rooms out of order or back into service", "rooms"),
    ("reservation.view", "View reservations", "reservations"),
    ("reservation.create", "Create reservations", "reservations"),
    ("reservation.modify", "Change reservations", "reservations"),
    ("reservation.cancel", "Cancel reservations", "reservations"),
    ("reservation.check_in", "Check guests in", "reservations"),
    ("reservation.check_out", "Check guests out", "reservations"),
    ("guest.view", "View guest profiles", "guests"),
    ("guest.view_contact", "View guest email addresses and phone numbers", "guests"),
    ("payment.view", "View payments", "payments"),
    ("payment.refund", "Issue refunds", "payments"),
    ("report.view", "View reports", "reports"),
    ("audit.view", "View the audit log", "audit"),
    ("settings.manage", "Change property settings", "settings"),
]
ALL_KEYS: set[str] = {key for key, _, _ in PERMISSIONS}


def _groups(*groups: str) -> set[str]:
    return {key for key, _, group in PERMISSIONS if group in groups}


ROLES: list[tuple[str, str, set[str]]] = [
    ("Admin", "Full access, including settings", ALL_KEYS),
    ("General manager", "Everything except property settings", ALL_KEYS - {"settings.manage"}),
    (
        "Front office manager",
        "Runs the front desk: rooms, reservations, guests, payments and reports",
        _groups("dashboard", "rooms", "reservations", "guests", "payments")
        | {"report.view", "user.view"},
    ),
    (
        "Receptionist",
        "Day-to-day front desk work",
        {
            "dashboard.view",
            "reservation.view",
            "reservation.create",
            "reservation.modify",
            "reservation.check_in",
            "reservation.check_out",
            "guest.view",
            "guest.view_contact",
            "payment.view",
            "room.update_housekeeping_status",
        },
    ),
    (
        "Housekeeping supervisor",
        "Oversees housekeeping and room availability",
        {"dashboard.view", "room.update_housekeeping_status", "room.set_out_of_order"},
    ),
    (
        "Room attendant",
        "Updates the housekeeping status of rooms",
        {"room.update_housekeeping_status"},
    ),
]

# Old users.role value -> new role (upgrade).
OLD_TO_NEW_ROLE: dict[str, str] = {
    "front_desk": "Receptionist",
    "housekeeping": "Housekeeping supervisor",
    "manager": "General manager",
    "admin": "Admin",
}

# New role -> old users.role value (downgrade). Anything else becomes front_desk.
NEW_TO_OLD_ROLE: dict[str, str] = {
    "Admin": "admin",
    "General manager": "manager",
    "Front office manager": "manager",
    "Receptionist": "front_desk",
    "Housekeeping supervisor": "housekeeping",
    "Room attendant": "housekeeping",
}

OLD_ROLE_ENUM = postgresql.ENUM(
    "front_desk", "housekeeping", "manager", "admin", name="user_role", create_type=False
)


def _property_defaults() -> tuple[str, str]:
    """Name and time zone for the first property: the app settings, if available."""
    try:
        from app.core.config import get_settings

        settings = get_settings()
        return settings.hotel_name, settings.hotel_timezone
    except Exception:  # settings unavailable: fall back to the development defaults
        return "Hotel", "Europe/Stockholm"


# Lightweight table definitions for data steps (independent of the ORM models).
properties_t = sa.table(
    "properties", sa.column("id", sa.Integer), sa.column("name"), sa.column("time_zone")
)
permissions_t = sa.table(
    "permissions", sa.column("key"), sa.column("description"), sa.column("group")
)
roles_t = sa.table(
    "roles",
    sa.column("id", sa.Integer),
    sa.column("property_id", sa.Integer),
    sa.column("name"),
    sa.column("description"),
    sa.column("is_system", sa.Boolean),
)
role_permissions_t = sa.table(
    "role_permissions", sa.column("role_id", sa.Integer), sa.column("permission_key")
)


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("This migration moves data and needs a live database (no --sql).")
    # 1. New tables ----------------------------------------------------------------------
    op.create_table(
        "properties",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_properties")),
    )
    op.create_table(
        "permissions",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("group", sa.String(length=50), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_permissions")),
    )
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("property_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["property_id"],
            ["properties.id"],
            name=op.f("fk_roles_property_id_properties"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.UniqueConstraint("property_id", "name", name="uq_roles_property_id_name"),
    )
    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("permission_key", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(
            ["permission_key"],
            ["permissions.key"],
            name=op.f("fk_role_permissions_permission_key_permissions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name=op.f("fk_role_permissions_role_id_roles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_key", name=op.f("pk_role_permissions")),
    )
    op.create_index(
        op.f("ix_role_permissions_permission_key"), "role_permissions", ["permission_key"]
    )
    op.create_table(
        "user_property_roles",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("property_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("assigned_by", sa.Integer(), nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by"],
            ["users.id"],
            name=op.f("fk_user_property_roles_assigned_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["property_id"],
            ["properties.id"],
            name=op.f("fk_user_property_roles_property_id_properties"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name=op.f("fk_user_property_roles_role_id_roles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_property_roles_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "property_id", name=op.f("pk_user_property_roles")),
    )
    op.create_index(
        op.f("ix_user_property_roles_property_id"), "user_property_roles", ["property_id"]
    )
    op.create_index(op.f("ix_user_property_roles_role_id"), "user_property_roles", ["role_id"])

    # 2. New user columns -------------------------------------------------------------------
    op.add_column("users", sa.Column("email", sa.String(length=320), nullable=True))
    op.add_column("users", sa.Column("phone", sa.String(length=50), nullable=True))
    op.add_column("users", sa.Column("job_title", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("employee_id", sa.String(length=50), nullable=True))
    op.add_column("users", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("deactivated_by", sa.Integer(), nullable=True))
    op.create_unique_constraint(op.f("uq_users_email"), "users", ["email"])
    op.create_check_constraint(op.f("ck_users_email_lowercase"), "users", "email = lower(email)")
    op.create_foreign_key(
        op.f("fk_users_deactivated_by_users"),
        "users",
        "users",
        ["deactivated_by"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3. One property for the existing data --------------------------------------------------
    bind = op.get_bind()
    name, time_zone = _property_defaults()
    property_id = bind.execute(
        sa.insert(properties_t).values(name=name, time_zone=time_zone).returning(properties_t.c.id)
    ).scalar_one()

    # 4. Permission catalog -----------------------------------------------------------------
    op.bulk_insert(
        permissions_t,
        [{"key": k, "description": d, "group": g} for k, d, g in PERMISSIONS],
    )

    # 5. Default system roles ---------------------------------------------------------------
    for role_name, description, keys in ROLES:
        role_id = bind.execute(
            sa.insert(roles_t)
            .values(
                property_id=property_id,
                name=role_name,
                description=description,
                is_system=True,
            )
            .returning(roles_t.c.id)
        ).scalar_one()
        op.bulk_insert(
            role_permissions_t,
            [{"role_id": role_id, "permission_key": key} for key in sorted(keys)],
        )

    # 6. Existing users get the role matching their old enum value --------------------------
    for old_role, new_role in OLD_TO_NEW_ROLE.items():
        bind.execute(
            sa.text(
                """
                INSERT INTO user_property_roles (user_id, property_id, role_id)
                SELECT u.id, :property_id, r.id
                FROM users u
                JOIN roles r ON r.property_id = :property_id AND r.name = :new_role
                WHERE u.role = CAST(:old_role AS user_role)
                """
            ),
            {"property_id": property_id, "old_role": old_role, "new_role": new_role},
        )

    # 7. Inactive users become deactivated ---------------------------------------------------
    op.execute("UPDATE users SET deactivated_at = now() WHERE NOT is_active")

    # 8. Drop the old columns -------------------------------------------------------------------
    op.drop_column("users", "role")
    op.drop_column("users", "is_active")
    postgresql.ENUM(name="user_role").drop(bind, checkfirst=True)


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("This migration moves data and needs a live database (no --sql).")
    bind = op.get_bind()

    # Restore the old columns (nullable first, filled from the new data).
    OLD_ROLE_ENUM.create(bind, checkfirst=True)
    op.add_column("users", sa.Column("role", OLD_ROLE_ENUM, nullable=True))
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    for new_role, old_role in NEW_TO_OLD_ROLE.items():
        bind.execute(
            sa.text(
                """
                UPDATE users
                SET role = CAST(:old_role AS user_role)
                FROM user_property_roles upr
                JOIN roles r ON r.id = upr.role_id
                WHERE upr.user_id = users.id
                  AND upr.property_id = (SELECT min(id) FROM properties)
                  AND r.name = :new_role
                """
            ),
            {"old_role": old_role, "new_role": new_role},
        )
    op.execute("UPDATE users SET role = 'front_desk' WHERE role IS NULL")
    op.alter_column("users", "role", nullable=False)
    op.execute("UPDATE users SET is_active = (deactivated_at IS NULL)")

    # Drop the new user columns.
    op.drop_constraint(op.f("fk_users_deactivated_by_users"), "users", type_="foreignkey")
    op.drop_constraint(op.f("ck_users_email_lowercase"), "users", type_="check")
    op.drop_constraint(op.f("uq_users_email"), "users", type_="unique")
    for column in (
        "deactivated_by",
        "deactivated_at",
        "last_login_at",
        "expires_at",
        "employee_id",
        "job_title",
        "phone",
        "email",
    ):
        op.drop_column("users", column)

    # Drop the new tables (dependents first).
    op.drop_index(op.f("ix_user_property_roles_role_id"), table_name="user_property_roles")
    op.drop_index(op.f("ix_user_property_roles_property_id"), table_name="user_property_roles")
    op.drop_table("user_property_roles")
    op.drop_index(op.f("ix_role_permissions_permission_key"), table_name="role_permissions")
    op.drop_table("role_permissions")
    op.drop_table("roles")
    op.drop_table("permissions")
    op.drop_table("properties")
