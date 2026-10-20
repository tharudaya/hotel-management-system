"""The consistency check (services/consistency.py) and the maintenance round."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.main import run_maintenance
from app.models import IdempotencyKey, ReservationStatus, Room, RoomType, User
from app.scripts.seed import seed_dev_data
from app.services import idempotency
from app.services.consistency import check_consistency
from app.services.permissions import get_default_property_id
from tests.conftest import live_clients
from tests.factories import new_reservation
from tests.test_realtime import seeded

__all__ = ["seeded"]

TODAY: date = date(2026, 3, 10)
RS = ReservationStatus


def day(offset: int) -> date:
    return TODAY + timedelta(days=offset)


@pytest.fixture
async def hotel(db_session: AsyncSession) -> tuple[int, RoomType, dict[str, Room]]:
    pid = await get_default_property_id(db_session)
    std = RoomType(property_id=pid, code="STD", name="Standard", max_occupancy=2)
    rooms = {n: Room(property_id=pid, number=n, floor=1, room_type=std) for n in ("101", "102")}
    db_session.add_all(rooms.values())
    await db_session.flush()
    return pid, std, rooms


async def test_the_seed_is_consistent(db_session: AsyncSession) -> None:
    await seed_dev_data(db_session, TODAY)
    pid = await get_default_property_id(db_session)

    report = await check_consistency(db_session, pid, TODAY)

    assert report.ok, report
    assert report.nights_checked > 60  # bookings run for months ahead


async def test_clean_data_passes(
    db_session: AsyncSession, hotel: tuple[int, RoomType, dict[str, Room]]
) -> None:
    pid, std, rooms = hotel
    db_session.add_all(
        [
            new_reservation(std, check_in_date=day(0), check_out_date=day(2), room=rooms["101"]),
            new_reservation(std, check_in_date=day(2), check_out_date=day(4), room=rooms["101"]),
            new_reservation(std, check_in_date=day(0), check_out_date=day(3)),
        ]
    )
    await db_session.flush()

    report = await check_consistency(db_session, pid, TODAY)

    assert report.ok
    assert report.nights_checked == 4


async def test_oversold_nights_are_reported(
    db_session: AsyncSession, hotel: tuple[int, RoomType, dict[str, Room]]
) -> None:
    pid, std, _ = hotel
    for _ in range(3):  # three bookings, two rooms, on day 1 only
        db_session.add(new_reservation(std, check_in_date=day(1), check_out_date=day(2)))
    # Cancelled and past bookings do not count.
    db_session.add(
        new_reservation(std, check_in_date=day(1), check_out_date=day(2), status=RS.CANCELLED)
    )
    await db_session.flush()

    report = await check_consistency(db_session, pid, TODAY)

    assert [(o.room_type, o.night, o.over_by) for o in report.oversold] == [("STD", day(1), 1)]
    assert not report.ok


async def test_a_booking_in_a_room_an_overstaying_guest_is_in_is_reported(
    db_session: AsyncSession, hotel: tuple[int, RoomType, dict[str, Room]]
) -> None:
    pid, std, rooms = hotel
    stayer = new_reservation(
        std, check_in_date=day(-3), check_out_date=day(-1), status=RS.CHECKED_IN, room=rooms["101"]
    )
    arrival = new_reservation(std, check_in_date=day(0), check_out_date=day(2), room=rooms["101"])
    db_session.add_all([stayer, arrival])
    await db_session.flush()

    report = await check_consistency(db_session, pid, TODAY)

    assert [(c.room_number, c.first, c.second) for c in report.room_clashes] == [
        ("101", stayer.confirmation_code, arrival.confirmation_code)
    ]


async def test_overlapping_assignments_are_reported(
    db_session: AsyncSession, hotel: tuple[int, RoomType, dict[str, Room]]
) -> None:
    pid, std, rooms = hotel
    first = new_reservation(std, check_in_date=day(1), check_out_date=day(3), room=rooms["101"])
    second = new_reservation(std, check_in_date=day(2), check_out_date=day(4), room=rooms["102"])
    db_session.add_all([first, second])
    await db_session.flush()
    # The exclusion constraint forbids this; simulate data that got past it (an import).
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await db_session.execute(
        text("ALTER TABLE reservations DROP CONSTRAINT ex_reservations_room_no_overlap")
    )
    await db_session.execute(
        update(type(second)).where(type(second).id == second.id).values(room_id=rooms["101"].id)
    )

    report = await check_consistency(db_session, pid, TODAY)

    assert [(c.room_number, c.reason) for c in report.room_clashes] == [
        ("101", "overlapping stays")
    ]  # the test's transaction is rolled back, constraint included


async def test_price_mismatches_are_reported(
    db_session: AsyncSession, hotel: tuple[int, RoomType, dict[str, Room]]
) -> None:
    pid, std, _ = hotel
    wrong_total = new_reservation(std, check_in_date=day(1), check_out_date=day(3))
    missing_night = new_reservation(std, check_in_date=day(1), check_out_date=day(3))
    db_session.add_all([wrong_total, missing_night])
    await db_session.flush()
    await db_session.execute(
        update(type(wrong_total))
        .where(type(wrong_total).id == wrong_total.id)
        .values(total_minor=1)
    )
    await db_session.execute(
        text("DELETE FROM reservation_nights WHERE reservation_id = :id AND night_date = :night"),
        {"id": missing_night.id, "night": day(2)},
    )

    report = await check_consistency(db_session, pid, TODAY)

    assert sorted((p.confirmation_code, p.problem) for p in report.price_problems) == sorted(
        [
            (wrong_total.confirmation_code, "total 1 is not the sum of its nights (200000)"),
            (missing_night.confirmation_code, "1 night rows for a 2-night stay"),
        ]
    )


async def test_maintenance_deletes_old_idempotency_keys(
    test_settings: Settings,
    seeded: dict[str, int],
    live_db: async_sessionmaker[AsyncSession],
) -> None:
    async with live_db() as db:
        user = await db.scalar(select(User).where(User.username == "frontdesk"))
        assert user is not None
        now = datetime.now(UTC)
        for key, age in [("old-key-1", 25), ("new-key-1", 1)]:
            record = idempotency.new_record(
                key=key,
                user_id=user.id,
                endpoint="POST /api/reservations",
                body_hash="0" * 64,
                reference="reservation:1",
            )
            record.created_at = now - timedelta(hours=age)
            db.add(record)
        await db.commit()

    async with live_clients(test_settings) as make:
        app, _ = await make()
        await run_maintenance(app)

    async with live_db() as db:
        assert (await db.scalars(select(IdempotencyKey.key))).all() == ["new-key-1"]
