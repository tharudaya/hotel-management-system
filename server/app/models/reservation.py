from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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

from app.core.confirmation_codes import PATTERN as CONFIRMATION_CODE_PATTERN
from app.models.base import Base, TimestampMixin, pg_enum
from app.models.enums import ReservationSource, ReservationStatus
from app.models.guest import Guest
from app.models.room import Room, RoomType

# Statuses that hold inventory (and a room, if one is assigned).
ACTIVE_STATUSES_SQL: str = "status IN ('confirmed', 'checked_in')"


class Reservation(TimestampMixin, Base):
    """A stay of check_in_date..check_out_date nights of one room type.

    Dates are plain dates in the property time zone; the stay occupies the half-open
    range [check_in_date, check_out_date), so a guest leaving on day D and another
    arriving on day D do not overlap. The room is assigned later (at the latest at
    check-in). Never deleted; see "Reservations" in CLAUDE.md.
    """

    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("check_out_date > check_in_date", name="check_out_after_check_in"),
        CheckConstraint("adults >= 1", name="adults_at_least_one"),
        CheckConstraint("children >= 0", name="children_non_negative"),
        CheckConstraint("total_minor >= 0", name="total_non_negative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso_code"),
        CheckConstraint(
            f"confirmation_code ~ '{CONFIRMATION_CODE_PATTERN}'", name="confirmation_code_format"
        ),
        # Being (or having been) in house requires a room.
        CheckConstraint(
            "status NOT IN ('checked_in', 'checked_out') OR room_id IS NOT NULL",
            name="in_house_has_room",
        ),
        # Status timestamps are set exactly when the status says so. A no-show is
        # recorded in the cancelled_* columns too (the stay was given up).
        CheckConstraint(
            "(checked_in_at IS NOT NULL) = (status IN ('checked_in', 'checked_out'))",
            name="checked_in_at_matches_status",
        ),
        CheckConstraint(
            "(checked_out_at IS NOT NULL) = (status = 'checked_out')",
            name="checked_out_at_matches_status",
        ),
        CheckConstraint(
            "(cancelled_at IS NOT NULL) = (status IN ('cancelled', 'no_show'))",
            name="cancelled_at_matches_status",
        ),
        CheckConstraint(
            "cancelled_at IS NOT NULL OR (cancelled_by IS NULL AND cancellation_reason IS NULL)",
            name="cancellation_details_need_cancelled_at",
        ),
        # Room type, room and guest must belong to the reservation's property.
        ForeignKeyConstraint(
            ["room_type_id", "property_id"],
            ["room_types.id", "room_types.property_id"],
            name="fk_reservations_room_type_id_property_id_room_types",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["room_id", "property_id"],
            ["rooms.id", "rooms.property_id"],
            name="fk_reservations_room_id_property_id_rooms",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["guest_id", "property_id"],
            ["guests.id", "guests.property_id"],
            name="fk_reservations_guest_id_property_id_guests",
            ondelete="RESTRICT",
        ),
        # No two active reservations may hold the same room on the same night.
        # Requires the btree_gist extension (created in the migration).
        ExcludeConstraint(
            ("room_id", "="),
            (text("daterange(check_in_date, check_out_date)"), "&&"),
            name="ex_reservations_room_no_overlap",
            using="gist",
            where=text(f"room_id IS NOT NULL AND {ACTIVE_STATUSES_SQL}"),
        ),
        # At most one party in house per room, even if a stay overruns its dates.
        Index(
            "uq_reservations_room_in_house",
            "room_id",
            unique=True,
            postgresql_where=text("status = 'checked_in'"),
        ),
        # Availability: active stays of a room type overlapping a date range.
        Index(
            "ix_reservations_availability",
            "property_id",
            "room_type_id",
            text("daterange(check_in_date, check_out_date)"),
            postgresql_using="gist",
            postgresql_where=text(ACTIVE_STATUSES_SQL),
        ),
        # Dashboard queries: arrivals (status, check_in_date), departures (status, check_out_date).
        Index("ix_reservations_status_check_in_date", "status", "check_in_date"),
        Index("ix_reservations_status_check_out_date", "status", "check_out_date"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="RESTRICT"))
    # Random, see app/core/confirmation_codes.py; unique across properties.
    confirmation_code: Mapped[str] = mapped_column(String(8), unique=True)
    guest_id: Mapped[int] = mapped_column(index=True)
    room_type_id: Mapped[int] = mapped_column(index=True)
    room_id: Mapped[int | None] = mapped_column(index=True)
    check_in_date: Mapped[date]
    check_out_date: Mapped[date]
    adults: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    children: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    status: Mapped[ReservationStatus] = mapped_column(
        pg_enum(ReservationStatus, "reservation_status"),
        default=ReservationStatus.CONFIRMED,
        server_default=ReservationStatus.CONFIRMED.value,
    )
    source: Mapped[ReservationSource] = mapped_column(
        pg_enum(ReservationSource, "reservation_source")
    )
    is_vip: Mapped[bool] = mapped_column(default=False, server_default=false())
    notes: Mapped[str | None] = mapped_column(Text)
    # Sum of the nights' rates, in minor units of `currency` (the property's at booking).
    total_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    # Null for reservations made by the system (seed, imports).
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    # Optimistic locking, as on rooms: a concurrent change raises StaleDataError.
    version: Mapped[int] = mapped_column(default=1, server_default="1")

    # Joined on the ids alone; the composite foreign keys keep the property in step.
    guest: Mapped[Guest] = relationship(
        lazy="raise", primaryjoin="Reservation.guest_id == Guest.id", foreign_keys=[guest_id]
    )
    room_type: Mapped[RoomType] = relationship(
        lazy="raise",
        primaryjoin="Reservation.room_type_id == RoomType.id",
        foreign_keys=[room_type_id],
    )
    room: Mapped[Room | None] = relationship(
        lazy="raise", primaryjoin="Reservation.room_id == Room.id", foreign_keys=[room_id]
    )
    nights: Mapped[list[ReservationNight]] = relationship(
        lazy="raise", order_by="ReservationNight.night_date"
    )

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012

    def __repr__(self) -> str:
        return f"<Reservation {self.confirmation_code!r} {self.status}>"


class ReservationNight(Base):
    """One night of a reservation with the price snapshotted at booking time."""

    __tablename__ = "reservation_nights"
    __table_args__ = (CheckConstraint("rate_minor >= 0", name="rate_non_negative"),)

    # The primary key is the unique (reservation_id, night_date).
    reservation_id: Mapped[int] = mapped_column(
        ForeignKey("reservations.id", ondelete="RESTRICT"), primary_key=True
    )
    night_date: Mapped[date] = mapped_column(primary_key=True)
    # Minor units of the reservation's currency.
    rate_minor: Mapped[int]

    def __repr__(self) -> str:
        return f"<ReservationNight {self.night_date} {self.rate_minor}>"
