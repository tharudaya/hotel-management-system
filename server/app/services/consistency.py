"""Checks that the reservation data obeys its rules (see "Reservations" in CLAUDE.md).

The rules are enforced when data is written (room type locks, the exclusion constraint,
check constraints); this re-checks the stored result, for monitoring and after imports
or manual fixes:

- no room type is oversold: for every night from today on, active bookings <= sellable
  rooms (as the availability service counts them, so an out-of-order room counts
  tonight);
- no room is assigned to two stays that overlap, and no booking holds a room tonight
  that a guest who overstayed is still in;
- every booking has exactly one night row per night of its stay, and its total is the
  sum of those nights.

Run it with `python -m app.scripts.check_reservations`.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import Reservation, ReservationNight, ReservationStatus, Room, RoomType
from app.services.availability import ACTIVE_STATUSES, AvailabilityService


@dataclass(frozen=True)
class Oversold:
    room_type: str
    night: date
    # How many bookings more than rooms.
    over_by: int


@dataclass(frozen=True)
class RoomClash:
    room_number: str
    first: str  # confirmation codes
    second: str
    reason: str


@dataclass(frozen=True)
class PriceProblem:
    confirmation_code: str
    problem: str


@dataclass
class ConsistencyReport:
    today: date
    nights_checked: int = 0
    oversold: list[Oversold] = field(default_factory=list)
    room_clashes: list[RoomClash] = field(default_factory=list)
    price_problems: list[PriceProblem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.oversold or self.room_clashes or self.price_problems)


async def check_consistency(db: AsyncSession, property_id: int, today: date) -> ConsistencyReport:
    report = ConsistencyReport(today=today)
    await _check_inventory(db, property_id, today, report)
    await _check_rooms(db, property_id, today, report)
    await _check_prices(db, property_id, report)
    return report


async def _check_inventory(
    db: AsyncSession, property_id: int, today: date, report: ConsistencyReport
) -> None:
    last_check_out = await db.scalar(
        select(func.max(Reservation.check_out_date)).where(
            Reservation.property_id == property_id, Reservation.status.in_(ACTIVE_STATUSES)
        )
    )
    end = max(last_check_out or today, today + timedelta(days=1))
    nights = [today + timedelta(days=i) for i in range((end - today).days)]
    report.nights_checked = len(nights)
    free = await AvailabilityService(db, today).free_rooms_by_night(property_id, nights)
    rows = await db.execute(
        select(RoomType.id, RoomType.code).where(RoomType.property_id == property_id)
    )
    codes = {type_id: code for type_id, code in rows}
    report.oversold = sorted(
        (
            Oversold(room_type=codes[type_id], night=night, over_by=-count)
            for type_id, by_night in free.items()
            for night, count in by_night.items()
            if count < 0
        ),
        key=lambda o: (o.night, o.room_type),
    )


async def _check_rooms(
    db: AsyncSession, property_id: int, today: date, report: ConsistencyReport
) -> None:
    first, second = aliased(Reservation), aliased(Reservation)
    overlap = and_(
        first.check_in_date < second.check_out_date,
        second.check_in_date < first.check_out_date,
    )
    # A guest still in house after their check-out date occupies the room tonight.
    overstay_clash = and_(
        first.status == ReservationStatus.CHECKED_IN,
        first.check_out_date < today,
        second.check_in_date <= today,
        second.check_out_date > today,
    )
    rows = (
        await db.execute(
            select(Room.number, first.confirmation_code, second.confirmation_code, overlap)
            .join(first, first.room_id == Room.id)
            .join(second, and_(second.room_id == Room.id, second.id != first.id))
            .where(
                Room.property_id == property_id,
                first.status.in_(ACTIVE_STATUSES),
                second.status.in_(ACTIVE_STATUSES),
                or_(and_(overlap, first.id < second.id), overstay_clash),
            )
            .order_by(Room.number)
        )
    ).all()
    report.room_clashes = [
        RoomClash(
            room_number=number,
            first=a,
            second=b,
            reason="overlapping stays"
            if overlaps
            else "assigned to a room an overstaying guest is in",
        )
        for number, a, b, overlaps in rows
    ]


async def _check_prices(db: AsyncSession, property_id: int, report: ConsistencyReport) -> None:
    nights = (
        select(
            ReservationNight.reservation_id.label("reservation_id"),
            func.count().label("count"),
            func.min(ReservationNight.night_date).label("first"),
            func.max(ReservationNight.night_date).label("last"),
            func.sum(ReservationNight.rate_minor).label("total"),
        )
        .group_by(ReservationNight.reservation_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                Reservation.confirmation_code,
                Reservation.check_in_date,
                Reservation.check_out_date,
                Reservation.total_minor,
                nights.c.count,
                nights.c.first,
                nights.c.last,
                nights.c.total,
            )
            .outerjoin(nights, nights.c.reservation_id == Reservation.id)
            .where(Reservation.property_id == property_id)
        )
    ).all()
    for code, check_in, check_out, total, count, first_night, last_night, nights_total in rows:
        expected = (check_out - check_in).days
        if (
            (count or 0) != expected
            or first_night != check_in
            or (last_night != check_out - timedelta(days=1))
        ):
            report.price_problems.append(
                PriceProblem(code, f"{count or 0} night rows for a {expected}-night stay")
            )
        elif total != nights_total:
            report.price_problems.append(
                PriceProblem(code, f"total {total} is not the sum of its nights ({nights_total})")
            )
