from datetime import date

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.models import Reservation, ReservationStatus, Room, User, UserRole
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

    users = (await db_session.scalars(select(User))).all()

    assert {u.role for u in users} == set(UserRole)
    assert all(verify_password(dev_password(u.username), u.password_hash) for u in users)
