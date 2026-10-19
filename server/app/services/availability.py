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

Callers that write (booking, modification, room setup changes that take a room away
from a type) must hold the room type row lock (`lock_room_type`) while they check and
write, so concurrent changes cannot oversell.
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
from app.services.errors import NotFoundError
from app.services.stays import stay_nights, validate_stay

ACTIVE_STATUSES: tuple[ReservationStatus, ...] = (
    ReservationStatus.CONFIRMED,
    ReservationStatus.CHECKED_IN,
)


async def lock_room_type(db: AsyncSession, property_id: int, room_type_id: int) -> RoomType:
    """Load the room type with a row lock (SELECT ... FOR UPDATE) held until the
    transaction ends. Every change to a type's bookings or sellable rooms takes it
    first, so availability checked under the lock stays true until commit."""
    room_type = await db.scalar(
        select(RoomType)
        .where(RoomType.id == room_type_id, RoomType.property_id == property_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if room_type is None:
        raise NotFoundError(f"Room type {room_type_id} not found", code="room_type_not_found")
    return room_type


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
        per_night = self._free_rows(property_id, nights, exclude_reservation_id).subquery()
        rows = (
            await self.db.execute(
                select(RoomType, func.min(per_night.c.free))
                .join(per_night, per_night.c.room_type_id == RoomType.id)
                .where(RoomType.is_active, RoomType.max_occupancy >= adults + children)
                .group_by(RoomType.id)
                .order_by(RoomType.name)
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

    async def free_rooms_by_night(
        self,
        property_id: int,
        nights: list[date],
        *,
        room_type_ids: list[int] | None = None,
        exclude_reservation_id: int | None = None,
    ) -> dict[int, dict[date, int]]:
        """Free rooms per room type and night (negative when oversold), in one query.
        No validation: callers pass the nights they care about."""
        if not nights:
            return {}
        rows = (
            await self.db.execute(
                self._free_rows(property_id, nights, exclude_reservation_id, room_type_ids)
            )
        ).all()
        result: dict[int, dict[date, int]] = {}
        for room_type_id, night, free in rows:
            result.setdefault(room_type_id, {})[night] = free
        return result

    def _free_rows(
        self,
        property_id: int,
        nights: list[date],
        exclude_reservation_id: int | None,
        room_type_ids: list[int] | None = None,
    ) -> Select[int, date, int]:
        """(room_type_id, night, free) for each room type of the property and night."""
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
                    func.daterange(min(nights), max(nights), "[]")
                ),
                overstay,
            ),
        ]
        if exclude_reservation_id is not None:
            booked_filter.append(Reservation.id != exclude_reservation_id)
        if room_type_ids is not None:
            booked_filter.append(Reservation.room_type_id.in_(room_type_ids))
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

        all_nights = night_list("stay_nights")
        stay_night = all_nights.c.night
        free = (
            func.coalesce(rooms.c.sellable, 0)
            - case((stay_night == today, func.coalesce(rooms.c.out_of_order, 0)), else_=0)
            - func.coalesce(booked.c.booked, 0)
        )
        stmt = (
            select(
                RoomType.id.label("room_type_id"),
                stay_night.label("night"),
                free.label("free"),
            )
            .select_from(RoomType)
            .join(all_nights, true())
            .outerjoin(rooms, rooms.c.room_type_id == RoomType.id)
            .outerjoin(
                booked,
                and_(booked.c.room_type_id == RoomType.id, booked.c.night == stay_night),
            )
            .where(RoomType.property_id == property_id)
        )
        if room_type_ids is not None:
            stmt = stmt.where(RoomType.id.in_(room_type_ids))
        return stmt
