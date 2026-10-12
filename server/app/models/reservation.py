from datetime import date

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    String,
    Text,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, pg_enum
from app.models.enums import ReservationStatus
from app.models.guest import Guest
from app.models.room import Room, RoomType


class Reservation(TimestampMixin, Base):
    """A stay of check_in_date..check_out_date nights.

    Dates are plain dates; the stay occupies the half-open range
    [check_in_date, check_out_date), so a guest leaving on day D and another
    arriving on day D do not overlap.
    """

    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("check_out_date > check_in_date", name="check_out_after_check_in"),
        CheckConstraint("adults >= 1", name="adults_at_least_one"),
        CheckConstraint("children >= 0", name="children_non_negative"),
        # Being (or having been) in house requires a room.
        CheckConstraint(
            "status NOT IN ('checked_in', 'checked_out') OR room_id IS NOT NULL",
            name="in_house_has_room",
        ),
        # No two active reservations may hold the same room on the same night.
        # Requires the btree_gist extension (created in the migration).
        ExcludeConstraint(
            ("room_id", "="),
            (text("daterange(check_in_date, check_out_date)"), "&&"),
            name="ex_reservations_room_no_overlap",
            using="gist",
            where=text("room_id IS NOT NULL AND status IN ('confirmed', 'checked_in')"),
        ),
        # At most one party in house per room, even if a stay overruns its dates.
        Index(
            "uq_reservations_room_in_house",
            "room_id",
            unique=True,
            postgresql_where=text("status = 'checked_in'"),
        ),
        # Dashboard queries: arrivals (status, check_in_date), departures (status, check_out_date).
        Index("ix_reservations_status_check_in_date", "status", "check_in_date"),
        Index("ix_reservations_status_check_out_date", "status", "check_out_date"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    confirmation_code: Mapped[str] = mapped_column(String(20), unique=True)
    guest_id: Mapped[int] = mapped_column(ForeignKey("guests.id", ondelete="RESTRICT"), index=True)
    room_type_id: Mapped[int] = mapped_column(
        ForeignKey("room_types.id", ondelete="RESTRICT"), index=True
    )
    room_id: Mapped[int | None] = mapped_column(
        ForeignKey("rooms.id", ondelete="RESTRICT"), index=True
    )
    check_in_date: Mapped[date]
    check_out_date: Mapped[date]
    adults: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    children: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    status: Mapped[ReservationStatus] = mapped_column(
        pg_enum(ReservationStatus, "reservation_status"),
        default=ReservationStatus.CONFIRMED,
        server_default=ReservationStatus.CONFIRMED.value,
    )
    is_vip: Mapped[bool] = mapped_column(default=False, server_default=false())
    notes: Mapped[str | None] = mapped_column(Text)

    guest: Mapped[Guest] = relationship(lazy="raise")
    room_type: Mapped[RoomType] = relationship(lazy="raise")
    room: Mapped[Room | None] = relationship(lazy="raise")

    def __repr__(self) -> str:
        return f"<Reservation {self.confirmation_code!r} {self.status}>"
