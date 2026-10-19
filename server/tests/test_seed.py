import re
from collections import Counter
from datetime import date, timedelta

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.confirmation_codes import PATTERN
from app.core.permissions import DEFAULT_ROLES
from app.core.security import verify_password
from app.models import (
    HousekeepingStatus,
    Reservation,
    ReservationSource,
    ReservationStatus,
    Role,
    Room,
    User,
    UserPropertyRole,
)
from app.scripts.seed import dev_password, seed_dev_data

TODAY: date = date(2026, 1, 15)


async def test_seed_creates_dashboard_mix(db_session: AsyncSession) -> None:
    summary = await seed_dev_data(db_session, TODAY)

    assert await db_session.scalar(select(func.count()).select_from(Room)) == 40
    assert sum(len(rooms) for rooms in summary.scenario_rooms.values()) == 40
    assert all(summary.scenario_rooms.values()), "every scenario has at least one room"

    async def count(*criteria: ColumnElement[bool]) -> int:
        query = select(func.count()).select_from(Reservation).where(*criteria)
        return await db_session.scalar(query) or 0

    in_house = Reservation.status == ReservationStatus.CHECKED_IN
    confirmed = Reservation.status == ReservationStatus.CONFIRMED

    # stayover + departing + turnover
    assert await count(in_house) == 12 + 6 + 2
    # departing + turnover
    assert await count(in_house, Reservation.check_out_date == TODAY) == 6 + 2
    # turnover + pre-assigned + unassigned
    assert await count(confirmed, Reservation.check_in_date == TODAY) == 2 + 6 + 3


async def test_seed_creates_one_user_per_role_with_dev_password(db_session: AsyncSession) -> None:
    await seed_dev_data(db_session, TODAY)

    rows = (
        await db_session.execute(
            select(User, Role.name)
            .join(UserPropertyRole, UserPropertyRole.user_id == User.id)
            .join(Role, Role.id == UserPropertyRole.role_id)
        )
    ).all()

    assert sorted(role for _, role in rows) == sorted(r.name for r in DEFAULT_ROLES)
    assert all(
        u.password_hash is not None and verify_password(dev_password(u.username), u.password_hash)
        for u, _ in rows
    )


async def test_seed_never_oversells_and_prices_every_night(db_session: AsyncSession) -> None:
    await seed_dev_data(db_session, TODAY)

    rooms = (await db_session.scalars(select(Room))).all()
    capacity = Counter(r.room_type_id for r in rooms)
    out_of_order = Counter(
        r.room_type_id for r in rooms if r.housekeeping_status is HousekeepingStatus.OUT_OF_ORDER
    )
    reservations = (
        await db_session.scalars(select(Reservation).options(selectinload(Reservation.nights)))
    ).all()

    booked: Counter[tuple[int, date]] = Counter()
    for r in reservations:
        stay = [
            r.check_in_date + timedelta(days=i)
            for i in range((r.check_out_date - r.check_in_date).days)
        ]
        assert [n.night_date for n in r.nights] == stay
        assert r.total_minor == sum(n.rate_minor for n in r.nights) > 0
        assert r.currency == "SEK"
        assert re.fullmatch(PATTERN, r.confirmation_code)
        if r.status in (ReservationStatus.CONFIRMED, ReservationStatus.CHECKED_IN):
            for night in stay:
                booked[(r.room_type_id, night)] += 1

    for (room_type_id, night), count in booked.items():
        sellable = capacity[room_type_id] - (out_of_order[room_type_id] if night == TODAY else 0)
        assert count <= sellable, (room_type_id, night)


async def test_seed_has_past_and_future_reservations(db_session: AsyncSession) -> None:
    summary = await seed_dev_data(db_session, TODAY)

    reservations = (await db_session.scalars(select(Reservation))).all()
    statuses = Counter(r.status for r in reservations)
    assert all(statuses[s] for s in ReservationStatus), statuses
    assert any(r.check_out_date < TODAY for r in reservations if r.status == "checked_out")
    assert sum(1 for r in reservations if r.check_in_date > TODAY) >= 50
    assert summary.future_bookings >= 50
    # Some guests come back.
    guests = Counter(r.guest_id for r in reservations)
    assert any(count > 1 for count in guests.values())
    assert {r.source for r in reservations} == set(ReservationSource)
