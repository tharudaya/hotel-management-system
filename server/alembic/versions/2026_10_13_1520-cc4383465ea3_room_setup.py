"""room setup

Room types and rooms become per-property, retirable records (see "Room setup" in
CLAUDE.md):

room_types: + property_id (backfilled with the existing property), description,
  bed_setup, is_active, created_at, updated_at. code and name are unique per property
  instead of globally.
rooms: + property_id (backfilled from the room's type), notes, is_active, retired_at,
  retired_by, created_at. number is unique per property instead of globally. The room
  type foreign key becomes (room_type_id, property_id), so a room can only use a room
  type of its own property. A check keeps is_active and retired_at consistent.

The room.manage permission needs no migration: the startup catalog sync inserts it
and grants it to Admin roles only.

Downgrade restores the global uniqueness of room numbers and type codes; it fails if
two properties use the same number or code by then.

Revision ID: cc4383465ea3
Revises: ff3cb811073d
Create Date: 2026-10-13 15:20:41.236515

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cc4383465ea3"
down_revision: str | Sequence[str] | None = "ff3cb811073d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    has_rows = conn.execute(sa.text("SELECT EXISTS (SELECT 1 FROM room_types)")).scalar()
    has_property = conn.execute(sa.text("SELECT EXISTS (SELECT 1 FROM properties)")).scalar()
    if has_rows and not has_property:
        raise RuntimeError("Room types exist but no property does; cannot assign them one.")

    # --- room_types -------------------------------------------------------------------
    op.add_column("room_types", sa.Column("property_id", sa.Integer(), nullable=True))
    op.execute("UPDATE room_types SET property_id = (SELECT min(id) FROM properties)")
    op.alter_column("room_types", "property_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_room_types_property_id_properties"),
        "room_types",
        "properties",
        ["property_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column("room_types", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("room_types", sa.Column("bed_setup", sa.String(length=100), nullable=True))
    op.add_column(
        "room_types",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    for column in ("created_at", "updated_at"):
        op.add_column(
            "room_types",
            sa.Column(
                column, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
            ),
        )
    op.drop_constraint(op.f("uq_room_types_code"), "room_types", type_="unique")
    op.create_unique_constraint(
        "uq_room_types_property_id_code", "room_types", ["property_id", "code"]
    )
    op.create_unique_constraint(
        "uq_room_types_property_id_name", "room_types", ["property_id", "name"]
    )
    op.create_unique_constraint("uq_room_types_id_property_id", "room_types", ["id", "property_id"])

    # --- rooms ------------------------------------------------------------------------
    op.add_column("rooms", sa.Column("property_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE rooms SET property_id = rt.property_id "
        "FROM room_types rt WHERE rt.id = rooms.room_type_id"
    )
    op.alter_column("rooms", "property_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_rooms_property_id_properties"),
        "rooms",
        "properties",
        ["property_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(op.f("fk_rooms_room_type_id_room_types"), "rooms", type_="foreignkey")
    op.create_foreign_key(
        "fk_rooms_room_type_id_property_id_room_types",
        "rooms",
        "room_types",
        ["room_type_id", "property_id"],
        ["id", "property_id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(op.f("uq_rooms_number"), "rooms", type_="unique")
    op.create_unique_constraint("uq_rooms_property_id_number", "rooms", ["property_id", "number"])
    op.add_column("rooms", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column(
        "rooms",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column("rooms", sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("rooms", sa.Column("retired_by", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_rooms_retired_by_users"),
        "rooms",
        "users",
        ["retired_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "rooms",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_check_constraint(
        op.f("ck_rooms_retired_consistent"),
        "rooms",
        "(is_active AND retired_at IS NULL) OR (NOT is_active AND retired_at IS NOT NULL)",
    )


def downgrade() -> None:
    """Downgrade schema."""
    # --- rooms ------------------------------------------------------------------------
    op.drop_constraint(op.f("ck_rooms_retired_consistent"), "rooms", type_="check")
    op.drop_column("rooms", "created_at")
    op.drop_constraint(op.f("fk_rooms_retired_by_users"), "rooms", type_="foreignkey")
    for column in ("retired_by", "retired_at", "is_active", "notes"):
        op.drop_column("rooms", column)
    op.drop_constraint("uq_rooms_property_id_number", "rooms", type_="unique")
    op.create_unique_constraint(op.f("uq_rooms_number"), "rooms", ["number"])
    op.drop_constraint("fk_rooms_room_type_id_property_id_room_types", "rooms", type_="foreignkey")
    op.create_foreign_key(
        op.f("fk_rooms_room_type_id_room_types"),
        "rooms",
        "room_types",
        ["room_type_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(op.f("fk_rooms_property_id_properties"), "rooms", type_="foreignkey")
    op.drop_column("rooms", "property_id")

    # --- room_types -------------------------------------------------------------------
    op.drop_constraint("uq_room_types_id_property_id", "room_types", type_="unique")
    op.drop_constraint("uq_room_types_property_id_name", "room_types", type_="unique")
    op.drop_constraint("uq_room_types_property_id_code", "room_types", type_="unique")
    op.create_unique_constraint(op.f("uq_room_types_code"), "room_types", ["code"])
    for column in ("updated_at", "created_at", "is_active", "bed_setup", "description"):
        op.drop_column("room_types", column)
    op.drop_constraint(
        op.f("fk_room_types_property_id_properties"), "room_types", type_="foreignkey"
    )
    op.drop_column("room_types", "property_id")
