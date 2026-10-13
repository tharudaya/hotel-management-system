from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, pg_enum
from app.models.enums import HousekeepingStatus


class RoomType(TimestampMixin, Base):
    """A kind of room (e.g. "DLX" Deluxe) at one property.

    Never deleted, only retired (is_active = false); see "Room setup" in CLAUDE.md.
    """

    __tablename__ = "room_types"
    __table_args__ = (
        CheckConstraint("max_occupancy > 0", name="max_occupancy_positive"),
        UniqueConstraint("property_id", "code", name="uq_room_types_property_id_code"),
        UniqueConstraint("property_id", "name", name="uq_room_types_property_id_name"),
        # Target of rooms' (room_type_id, property_id) foreign key, so a room can only
        # use a room type of its own property.
        UniqueConstraint("id", "property_id", name="uq_room_types_id_property_id"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="RESTRICT"))
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    max_occupancy: Mapped[int] = mapped_column(SmallInteger)
    description: Mapped[str | None] = mapped_column(Text)
    # e.g. "1 king" or "2 singles"
    bed_setup: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())

    def __repr__(self) -> str:
        return f"<RoomType {self.code!r}>"


class Room(Base):
    """A physical room.

    Occupancy is not stored here: a room is occupied when a reservation with
    status checked_in is assigned to it. Never deleted, only retired (is_active =
    false, retired_at/retired_by set); see "Room setup" in CLAUDE.md.
    """

    __tablename__ = "rooms"
    __table_args__ = (
        CheckConstraint("floor >= 0", name="floor_non_negative"),
        # Retired exactly when retired_at is set.
        CheckConstraint(
            "(is_active AND retired_at IS NULL) OR (NOT is_active AND retired_at IS NOT NULL)",
            name="retired_consistent",
        ),
        UniqueConstraint("property_id", "number", name="uq_rooms_property_id_number"),
        ForeignKeyConstraint(
            ["room_type_id", "property_id"],
            ["room_types.id", "room_types.property_id"],
            name="fk_rooms_room_type_id_property_id_room_types",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="RESTRICT"))
    # String, not int: hotels use numbers like "PH1" or "012".
    number: Mapped[str] = mapped_column(String(10))
    floor: Mapped[int] = mapped_column(SmallInteger, index=True)
    room_type_id: Mapped[int] = mapped_column(index=True)
    housekeeping_status: Mapped[HousekeepingStatus] = mapped_column(
        pg_enum(HousekeepingStatus, "housekeeping_status"),
        default=HousekeepingStatus.DIRTY,
        server_default=HousekeepingStatus.DIRTY.value,
    )
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Optimistic locking: SQLAlchemy adds "AND version = <loaded>" to every
    # UPDATE and bumps it; a concurrent change raises StaleDataError.
    version: Mapped[int] = mapped_column(default=1, server_default="1")

    # Joined on the id alone; the composite foreign key above keeps the property in step.
    room_type: Mapped[RoomType] = relationship(
        lazy="raise",
        primaryjoin="Room.room_type_id == RoomType.id",
        foreign_keys=[room_type_id],
    )

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012

    def __repr__(self) -> str:
        return f"<Room {self.number!r} {self.housekeeping_status}>"
