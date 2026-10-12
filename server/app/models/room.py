from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    SmallInteger,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, pg_enum
from app.models.enums import HousekeepingStatus


class RoomType(Base):
    __tablename__ = "room_types"
    __table_args__ = (CheckConstraint("max_occupancy > 0", name="max_occupancy_positive"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    max_occupancy: Mapped[int] = mapped_column(SmallInteger)

    def __repr__(self) -> str:
        return f"<RoomType {self.code!r}>"


class Room(Base):
    """A physical room.

    Occupancy is not stored here: a room is occupied when a reservation with
    status checked_in is assigned to it.
    """

    __tablename__ = "rooms"
    __table_args__ = (CheckConstraint("floor >= 0", name="floor_non_negative"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    # String, not int: hotels use numbers like "PH1" or "012".
    number: Mapped[str] = mapped_column(String(10), unique=True)
    floor: Mapped[int] = mapped_column(SmallInteger, index=True)
    room_type_id: Mapped[int] = mapped_column(
        ForeignKey("room_types.id", ondelete="RESTRICT"), index=True
    )
    housekeeping_status: Mapped[HousekeepingStatus] = mapped_column(
        pg_enum(HousekeepingStatus, "housekeeping_status"),
        default=HousekeepingStatus.DIRTY,
        server_default=HousekeepingStatus.DIRTY.value,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Optimistic locking: SQLAlchemy adds "AND version = <loaded>" to every
    # UPDATE and bumps it; a concurrent change raises StaleDataError.
    version: Mapped[int] = mapped_column(default=1, server_default="1")

    room_type: Mapped[RoomType] = relationship(lazy="raise")

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012

    def __repr__(self) -> str:
        return f"<Room {self.number!r} {self.housekeeping_status}>"
