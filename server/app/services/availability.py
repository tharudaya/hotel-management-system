"""Availability per room type for a stay (see "Reservations" in CLAUDE.md).

For each active room type that fits the party, and each night of the stay:

    free(night) = sellable(night) - booked(night)

- sellable = active (not retired) rooms of the type. Rooms that are out of order now
  are not sellable tonight, unless a guest is in house in them (that guest is already
  counted as booked). Known simplification: out of order has no dates, so it only
  reduces tonight; future nights count every active room.
- booked = confirmed and checked-in reservations of the type covering the night
  (check_in_date <= night < check_out_date, so back-to-back stays never conflict),
  plus, for tonight, guests in house whose check-out date is before today (overstays
  still hold a room; guests due out today do not, their rooms are sold tonight).
  Cancelled, no-show and checked-out stays hold nothing.

The stay's availability is the smallest free(night), never below 0. Everything is
computed in one query: the nights are a VALUES list joined to the counts.

Callers that write (booking, modification) must hold the room type row lock while they
check and write, so concurrent bookings cannot oversell; this module only reads.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Date, Select, and_, case, column, exists, func, or_, select, true, values
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import Values

from app.models import (
    HousekeepingStatus,
    Property,
    Reservation,
    ReservationStatus,
    Room,
    RoomType,
)
from app.schemas.availability import Availability, NightlyRate, RoomTypeAvailability
from app.services.stays import stay_nights, validate_stay

ACTIVE_STATUSES: tuple[ReservationStatus, ...] = (
    ReservationStatus.CONFIRMED,
    ReservationStatus.CHECKED_IN,
)


@dataclass
class AvailabilityService:
    db: AsyncSession
    # The hotel's business date.
    today: date

    async def available_by_room_type(
        self,
        property_id: int,
        check_in_date: date,
        check_out_date: date,
        adults: int,
        children: int,
        exclude_reservation_id: int | None = None,
    ) -> Availability:
        """Validates the stay, then counts. `exclude_reservation_id` ignores that booking
        (a modification must not compete with itself)."""
        prop = await self.db.get_one(Property, property_id)
        validate_stay(
            prop,
            self.today,
            check_in_date=check_in_date,
            check_out_date=check_out_date,
            adults=adults,
            children=children,
        )
        nights = stay_nights(check_in_date, check_out_date)
        rows = (
            await self.db.execute(
                self._query(property_id, nights, adults + children, exclude_reservation_id)
            )
        ).all()
        return Availability(
            check_in_date=check_in_date,
            check_out_date=check_out_date,
            nights=len(nights),
            adults=adults,
            children=children,
            currency=prop.currency,
            room_types=[
                RoomTypeAvailability(
                    room_type_id=room_type.id,
                    code=room_type.code,
                    name=room_type.name,
                    max_occupancy=room_type.max_occupancy,
                    bed_setup=room_type.bed_setup,
                    available=max(free, 0),
                    nightly_rates=[
                        NightlyRate(night_date=night, rate_minor=room_type.base_rate_minor)
                        for night in nights
                    ],
                    total_minor=room_type.base_rate_minor * len(nights),
                    currency=prop.currency,
                )
                for room_type, free in rows
            ],
        )

    def _query(
        self,
        property_id: int,
        nights: list[date],
        guests: int,
        exclude_reservation_id: int | None,
    ) -> Select[RoomType, int]:
        today = self.today

        def night_list(name: str) -> Values:
            return values(column("night", Date), name=name).data([(n,) for n in nights])

        # Booked rooms per (type, night).
        booked_nights = night_list("booked_nights")
        night = booked_nights.c.night
        # Due out before today and still in house. Guests due out today are expected to
        # leave, so their rooms are sold tonight (turnover).
        overstay = and_(
            Reservation.status == ReservationStatus.CHECKED_IN,
            Reservation.check_out_date < today,
        )
        booked_filter = [
            Reservation.property_id == property_id,
            Reservation.status.in_(ACTIVE_STATUSES),
            # Prefilter (ix_reservations_availability, and the status index for overstays).
            or_(
                func.daterange(Reservation.check_in_date, Reservation.check_out_date).op("&&")(
                    func.daterange(nights[0], nights[-1], "[]")
                ),
                overstay,
            ),
        ]
        if exclude_reservation_id is not None:
            booked_filter.append(Reservation.id != exclude_reservation_id)
        booked = (
            select(
                Reservation.room_type_id.label("room_type_id"),
                night.label("night"),
                func.count().label("booked"),
            )
            .select_from(booked_nights)
            .join(
                Reservation,
                or_(
                    and_(Reservation.check_in_date <= night, Reservation.check_out_date > night),
                    and_(overstay, night == today),
                ),
            )
            .where(*booked_filter)
            .group_by(Reservation.room_type_id, night)
            .subquery("booked")
        )

        # Sellable rooms per type, and those out of order now without a guest in them.
        guest_in_room = exists().where(
            Reservation.room_id == Room.id, Reservation.status == ReservationStatus.CHECKED_IN
        )
        rooms = (
            select(
                Room.room_type_id.label("room_type_id"),
                func.count().label("sellable"),
                func.count()
                .filter(Room.housekeeping_status == HousekeepingStatus.OUT_OF_ORDER, ~guest_in_room)
                .label("out_of_order"),
            )
            .where(Room.property_id == property_id, Room.is_active)
            .group_by(Room.room_type_id)
            .subquery("rooms")
        )

        stay_nights_list = night_list("stay_nights")
        stay_night = stay_nights_list.c.night
        free = (
            func.coalesce(rooms.c.sellable, 0)
            - case((stay_night == today, func.coalesce(rooms.c.out_of_order, 0)), else_=0)
            - func.coalesce(booked.c.booked, 0)
        )
        return (
            select(RoomType, func.min(free).label("free"))
            .select_from(RoomType)
            .join(stay_nights_list, true())
            .outerjoin(rooms, rooms.c.room_type_id == RoomType.id)
            .outerjoin(
                booked,
                and_(booked.c.room_type_id == RoomType.id, booked.c.night == stay_night),
            )
            .where(
                RoomType.property_id == property_id,
                RoomType.is_active,
                RoomType.max_occupancy >= guests,
            )
            .group_by(RoomType.id)
            .order_by(RoomType.name)
        )
