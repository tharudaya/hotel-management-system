"""front desk core tables

Users, room types, rooms, guests and reservations.

Revision ID: 62a7ab3d6916
Revises:
Create Date: 2026-10-12 08:50:52.032456

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "62a7ab3d6916"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Needed by ex_reservations_room_no_overlap: lets a GiST index compare
    # integers (room_id) with "=" alongside the date-range "&&" overlap check.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "guests",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guests")),
    )
    op.create_index(op.f("ix_guests_email"), "guests", ["email"], unique=False)
    op.create_index(
        "ix_guests_last_name_first_name", "guests", ["last_name", "first_name"], unique=False
    )
    op.create_table(
        "room_types",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("max_occupancy", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("max_occupancy > 0", name=op.f("ck_room_types_max_occupancy_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_room_types")),
        sa.UniqueConstraint("code", name=op.f("uq_room_types_code")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column(
            "role",
            sa.Enum("front_desk", "housekeeping", "manager", "admin", name="user_role"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("username = lower(username)", name=op.f("ck_users_username_lowercase")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )
    op.create_table(
        "rooms",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("number", sa.String(length=10), nullable=False),
        sa.Column("floor", sa.SmallInteger(), nullable=False),
        sa.Column("room_type_id", sa.Integer(), nullable=False),
        sa.Column(
            "housekeeping_status",
            sa.Enum(
                "dirty",
                "in_progress",
                "clean",
                "inspected",
                "out_of_order",
                name="housekeeping_status",
            ),
            server_default="dirty",
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("floor >= 0", name=op.f("ck_rooms_floor_non_negative")),
        sa.ForeignKeyConstraint(
            ["room_type_id"],
            ["room_types.id"],
            name=op.f("fk_rooms_room_type_id_room_types"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rooms")),
        sa.UniqueConstraint("number", name=op.f("uq_rooms_number")),
    )
    op.create_index(op.f("ix_rooms_floor"), "rooms", ["floor"], unique=False)
    op.create_index(op.f("ix_rooms_room_type_id"), "rooms", ["room_type_id"], unique=False)
    op.create_table(
        "reservations",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("confirmation_code", sa.String(length=20), nullable=False),
        sa.Column("guest_id", sa.Integer(), nullable=False),
        sa.Column("room_type_id", sa.Integer(), nullable=False),
        sa.Column("room_id", sa.Integer(), nullable=True),
        sa.Column("check_in_date", sa.Date(), nullable=False),
        sa.Column("check_out_date", sa.Date(), nullable=False),
        sa.Column("adults", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("children", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "confirmed",
                "checked_in",
                "checked_out",
                "cancelled",
                "no_show",
                name="reservation_status",
            ),
            server_default="confirmed",
            nullable=False,
        ),
        sa.Column("is_vip", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        postgresql.ExcludeConstraint(
            (sa.column("room_id"), "="),
            (sa.text("daterange(check_in_date, check_out_date)"), "&&"),
            where=sa.text("room_id IS NOT NULL AND status IN ('confirmed', 'checked_in')"),
            using="gist",
            name="ex_reservations_room_no_overlap",
        ),
        sa.CheckConstraint(
            "status NOT IN ('checked_in', 'checked_out') OR room_id IS NOT NULL",
            name=op.f("ck_reservations_in_house_has_room"),
        ),
        sa.CheckConstraint("adults >= 1", name=op.f("ck_reservations_adults_at_least_one")),
        sa.CheckConstraint(
            "check_out_date > check_in_date", name=op.f("ck_reservations_check_out_after_check_in")
        ),
        sa.CheckConstraint("children >= 0", name=op.f("ck_reservations_children_non_negative")),
        sa.ForeignKeyConstraint(
            ["guest_id"],
            ["guests.id"],
            name=op.f("fk_reservations_guest_id_guests"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["rooms.id"],
            name=op.f("fk_reservations_room_id_rooms"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["room_type_id"],
            ["room_types.id"],
            name=op.f("fk_reservations_room_type_id_room_types"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reservations")),
        sa.UniqueConstraint("confirmation_code", name=op.f("uq_reservations_confirmation_code")),
    )
    op.create_index(op.f("ix_reservations_guest_id"), "reservations", ["guest_id"], unique=False)
    op.create_index(op.f("ix_reservations_room_id"), "reservations", ["room_id"], unique=False)
    op.create_index(
        op.f("ix_reservations_room_type_id"), "reservations", ["room_type_id"], unique=False
    )
    op.create_index(
        "ix_reservations_status_check_in_date",
        "reservations",
        ["status", "check_in_date"],
        unique=False,
    )
    op.create_index(
        "ix_reservations_status_check_out_date",
        "reservations",
        ["status", "check_out_date"],
        unique=False,
    )
    op.create_index(
        "uq_reservations_room_in_house",
        "reservations",
        ["room_id"],
        unique=True,
        postgresql_where=sa.text("status = 'checked_in'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_reservations_room_in_house",
        table_name="reservations",
        postgresql_where=sa.text("status = 'checked_in'"),
    )
    op.drop_index("ix_reservations_status_check_out_date", table_name="reservations")
    op.drop_index("ix_reservations_status_check_in_date", table_name="reservations")
    op.drop_index(op.f("ix_reservations_room_type_id"), table_name="reservations")
    op.drop_index(op.f("ix_reservations_room_id"), table_name="reservations")
    op.drop_index(op.f("ix_reservations_guest_id"), table_name="reservations")
    op.drop_table("reservations")
    op.drop_index(op.f("ix_rooms_room_type_id"), table_name="rooms")
    op.drop_index(op.f("ix_rooms_floor"), table_name="rooms")
    op.drop_table("rooms")
    op.drop_table("users")
    op.drop_table("room_types")
    op.drop_index("ix_guests_last_name_first_name", table_name="guests")
    op.drop_index(op.f("ix_guests_email"), table_name="guests")
    op.drop_table("guests")

    # drop_table does not remove the native enum types created with the tables.
    bind = op.get_bind()
    for enum_name in ("reservation_status", "housekeeping_status", "user_role"):
        postgresql.ENUM(name=enum_name).drop(bind, checkfirst=True)
    # btree_gist is left installed: other objects may depend on it, and it is harmless.
