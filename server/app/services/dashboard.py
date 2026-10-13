"""Read side of the front desk dashboard: per-room status and daily summary.

Each read is a fixed number of queries regardless of the number of rooms
(rooms: 2, summary: 2), so there is no N+1.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Guest,
    HousekeepingStatus,
    Reservation,
    ReservationStatus,
    Room,
    RoomType,
)
from app.schemas.dashboard import (
    DashboardRoom,
    DashboardRooms,
    DashboardSummary,
    OccupancyStatus,
)


@dataclass(frozen=True)
class Stay:
    """The parts of a reservation the dashboard needs."""

    reservation_id: int
    guest_name: str
    check_in_date: date
    check_out_date: date
    is_vip: bool
    has_notes: bool


@dataclass(frozen=True)
class RoomOccupancy:
    status: OccupancyStatus
    stay: Stay | None
    nights_remaining: int | None
    has_arrival_today: bool


def derive_occupancy(
    housekeeping_status: HousekeepingStatus,
    in_house: Stay | None,
    arrival: Stay | None,
    today: date,
) -> RoomOccupancy:
    """Decide what the dashboard shows for one room.

    in_house: the checked-in reservation assigned to the room, if any.
    arrival: a confirmed reservation assigned to the room, if any.

    Precedence: a guest in house always wins (even in an out-of-order room, so
    nobody is overlooked), then out of order, then today's arrival, then vacant.
    """
    arriving = arrival if arrival is not None and arrival.check_in_date == today else None
    has_arrival_today = arriving is not None

    if in_house is not None:
        nights_left = (in_house.check_out_date - today).days
        status = OccupancyStatus.DEPARTING_TODAY if nights_left <= 0 else OccupancyStatus.OCCUPIED
        return RoomOccupancy(status, in_house, max(nights_left, 0), has_arrival_today)

    if housekeeping_status is HousekeepingStatus.OUT_OF_ORDER:
        return RoomOccupancy(OccupancyStatus.OUT_OF_ORDER, None, None, has_arrival_today)

    if arriving is not None:
        nights = (arriving.check_out_date - arriving.check_in_date).days
        return RoomOccupancy(OccupancyStatus.ARRIVING_TODAY, arriving, nights, True)

    return RoomOccupancy(OccupancyStatus.VACANT, None, None, False)


def _stay(reservation: Reservation, guest: Guest) -> Stay:
    return Stay(
        reservation_id=reservation.id,
        guest_name=f"{guest.first_name} {guest.last_name}",
        check_in_date=reservation.check_in_date,
        check_out_date=reservation.check_out_date,
        is_vip=reservation.is_vip,
        has_notes=bool(reservation.notes and reservation.notes.strip()),
    )


async def get_dashboard_rooms(
    db: AsyncSession, today: date, room_ids: Collection[int] | None = None
) -> DashboardRooms:
    """Dashboard entries for all rooms, or only `room_ids` if given."""
    # Retired rooms are not part of the hotel any more: no tile, no counts.
    room_query = (
        select(Room, RoomType)
        .join(Room.room_type)
        .where(Room.is_active)
        .order_by(Room.floor, Room.number)
    )
    reservation_scope = Reservation.room_id.is_not(None)
    if room_ids is not None:
        room_query = room_query.where(Room.id.in_(room_ids))
        reservation_scope = Reservation.room_id.in_(room_ids)
    # populate_existing: always report the database's current state, even for
    # objects this session already loaded (e.g. right after a mutation).
    rooms = (await db.execute(room_query.execution_options(populate_existing=True))).all()

    # Every reservation that can affect a room's status today, in one query.
    # The DB guarantees at most one checked-in and one confirmed-for-today per room.
    relevant = (
        await db.execute(
            select(Reservation, Guest)
            .join(Reservation.guest)
            .where(
                reservation_scope,
                or_(
                    Reservation.status == ReservationStatus.CHECKED_IN,
                    and_(
                        Reservation.status == ReservationStatus.CONFIRMED,
                        Reservation.check_in_date == today,
                    ),
                ),
            )
            .execution_options(populate_existing=True)
        )
    ).all()

    in_house: dict[int, Stay] = {}
    arrivals: dict[int, Stay] = {}
    for reservation, guest in relevant:
        if reservation.room_id is None:  # excluded by the query; narrows the type
            continue
        target = in_house if reservation.status is ReservationStatus.CHECKED_IN else arrivals
        target[reservation.room_id] = _stay(reservation, guest)

    entries: list[DashboardRoom] = []
    for room, room_type in rooms:
        occupancy = derive_occupancy(
            room.housekeeping_status, in_house.get(room.id), arrivals.get(room.id), today
        )
        stay = occupancy.stay
        entries.append(
            DashboardRoom(
                room_id=room.id,
                number=room.number,
                floor=room.floor,
                room_type=room_type.code,
                room_type_name=room_type.name,
                housekeeping_status=room.housekeeping_status,
                occupancy_status=occupancy.status,
                guest_name=stay.guest_name if stay else None,
                reservation_id=stay.reservation_id if stay else None,
                nights_remaining=occupancy.nights_remaining,
                is_vip=stay.is_vip if stay else False,
                has_notes=stay.has_notes if stay else False,
                has_arrival_today=occupancy.has_arrival_today,
                version=room.version,
            )
        )
    return DashboardRooms(business_date=today, rooms=entries)


async def get_dashboard_room(db: AsyncSession, today: date, room_id: int) -> DashboardRoom:
    [entry] = (await get_dashboard_rooms(db, today, [room_id])).rooms
    return entry


async def get_dashboard_summary(db: AsyncSession, today: date) -> DashboardSummary:
    status = Reservation.status
    in_house = status == ReservationStatus.CHECKED_IN
    checked_out = status == ReservationStatus.CHECKED_OUT
    confirmed = status == ReservationStatus.CONFIRMED
    arrives_today = Reservation.check_in_date == today
    leaves_today = Reservation.check_out_date == today
    # Overstays (due out on an earlier day, still in house) are still due out today.
    due_out = and_(in_house, Reservation.check_out_date <= today)

    counts = (
        await db.execute(
            select(
                func.count().filter(arrives_today, or_(confirmed, in_house)),
                func.count().filter(arrives_today, in_house),
                func.count().filter(or_(due_out, and_(checked_out, leaves_today))),
                func.count().filter(checked_out, leaves_today),
                func.count().filter(in_house),
                func.coalesce(
                    func.sum(Reservation.adults + Reservation.children).filter(in_house), 0
                ),
                # Sold for tonight: staying past today, or arriving today (assigned or not).
                func.count().filter(
                    or_(
                        and_(in_house, Reservation.check_out_date > today),
                        and_(confirmed, arrives_today),
                    )
                ),
            )
            .select_from(Reservation)
            .where(or_(in_house, arrives_today, leaves_today))
        )
    ).one()
    (
        arrivals,
        arrivals_in,
        departures,
        departures_out,
        in_house_rooms,
        in_house_guests,
        sold_tonight,
    ) = counts

    total_rooms, dirty, out_of_order = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(Room.housekeeping_status == HousekeepingStatus.DIRTY),
                func.count().filter(Room.housekeeping_status == HousekeepingStatus.OUT_OF_ORDER),
            )
            .select_from(Room)
            .where(Room.is_active)
        )
    ).one()

    sellable = total_rooms - out_of_order
    occupancy = round(sold_tonight * 100 / sellable, 1) if sellable else 0.0

    return DashboardSummary(
        business_date=today,
        arrivals_today=arrivals,
        arrivals_checked_in=arrivals_in,
        departures_today=departures,
        departures_checked_out=departures_out,
        in_house=in_house_rooms,
        in_house_guests=in_house_guests,
        available_tonight=max(sellable - sold_tonight, 0),
        dirty=dirty,
        out_of_order=out_of_order,
        occupancy_percent=occupancy,
    )
