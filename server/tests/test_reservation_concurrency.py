"""Concurrent bookings against the real test database (committed data, parallel requests).

The room type row lock serialises bookings of a type, so the availability each one
checks is still true when it commits: the last room is sold exactly once.
"""

import asyncio
import contextlib
import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import Reservation, Room, RoomType
from app.services import reservations as reservation_module
from app.services.availability import lock_room_type
from app.services.permissions import get_default_property_id
from tests.conftest import Roles, live_clients
from tests.factories import new_guest, new_reservation
from tests.test_realtime import TODAY, connect, live, login, receive, seeded

__all__ = ["seeded"]  # the fixture, re-used from the realtime tests

PARALLEL = 8


async def one_room_left(live_db: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    """(room type id, guest id): a type with two rooms, one already booked tonight."""
    async with live_db() as db:
        pid = await get_default_property_id(db)
        room_type = RoomType(
            property_id=pid, code="LST", name="Last room", max_occupancy=2, base_rate_minor=1000
        )
        db.add_all(
            [
                Room(property_id=pid, number="801", floor=8, room_type=room_type),
                Room(property_id=pid, number="802", floor=8, room_type=room_type),
            ]
        )
        guest = new_guest(pid, "Ada", "Guest")
        db.add(guest)
        db.add(
            new_reservation(
                room_type, check_in_date=TODAY, check_out_date=TODAY + timedelta(days=1)
            )
        )
        await db.commit()
        return room_type.id, guest.id


async def booked(live_db: async_sessionmaker[AsyncSession], room_type_id: int) -> int:
    async with live_db() as db:
        return (
            await db.scalar(
                select(func.count())
                .select_from(Reservation)
                .where(Reservation.room_type_id == room_type_id)
            )
            or 0
        )


def body(room_type_id: int, guest_id: int) -> dict[str, object]:
    return {
        "guest_id": guest_id,
        "room_type_id": room_type_id,
        "check_in_date": TODAY.isoformat(),
        "check_out_date": (TODAY + timedelta(days=2)).isoformat(),
        "adults": 1,
        "source": "phone",
    }


async def post(client: AsyncClient, data: dict[str, object], key: str) -> Response:
    return await client.post("/api/reservations", json=data, headers={"Idempotency-Key": key})


async def test_parallel_bookings_sell_the_last_room_once(
    test_settings: Settings,
    seeded: dict[str, int],
    live_db: async_sessionmaker[AsyncSession],
) -> None:
    room_type_id, guest_id = await one_room_left(live_db)
    async with live_clients(test_settings) as make:
        _, desk = await live(make)

        responses = await asyncio.gather(
            *(post(desk, body(room_type_id, guest_id), uuid.uuid4().hex) for _ in range(PARALLEL))
        )

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [201] + [409] * (PARALLEL - 1), [r.text for r in responses]
    assert {r.json()["code"] for r in responses if r.status_code == 409} == {"no_availability"}
    assert await booked(live_db, room_type_id) == 2  # the earlier booking and one new


async def test_parallel_retries_with_one_key_book_once(
    test_settings: Settings,
    seeded: dict[str, int],
    live_db: async_sessionmaker[AsyncSession],
) -> None:
    room_type_id, guest_id = await one_room_left(live_db)
    key = uuid.uuid4().hex
    async with live_clients(test_settings) as make:
        _, desk = await live(make)

        responses = await asyncio.gather(
            *(post(desk, body(room_type_id, guest_id), key) for _ in range(PARALLEL))
        )

    assert [r.status_code for r in responses] == [201] * PARALLEL, [r.text for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1
    assert sum(1 for r in responses if r.headers.get("Idempotent-Replayed") == "true") == (
        PARALLEL - 1
    )
    assert await booked(live_db, room_type_id) == 2


async def test_one_key_racing_on_two_room_types_books_once(
    test_settings: Settings,
    seeded: dict[str, int],
    live_db: async_sessionmaker[AsyncSession],
) -> None:
    """Different room types take different locks, so only the unique key decides: the
    loser rolls its booking back and answers like a retry (replay or 422)."""
    room_type_id, guest_id = await one_room_left(live_db)
    async with live_db() as db:
        other = RoomType(
            property_id=await get_default_property_id(db),
            code="OTH",
            name="Other",
            max_occupancy=2,
            base_rate_minor=1000,
        )
        db.add(Room(property_id=other.property_id, number="803", floor=8, room_type=other))
        await db.commit()
        other_id = other.id
    key = uuid.uuid4().hex
    async with live_clients(test_settings) as make:
        _, desk = await live(make)

        responses = await asyncio.gather(
            *(
                post(desk, body(type_id, guest_id), key)
                for type_id in [room_type_id, other_id] * (PARALLEL // 2)
            )
        )

    created = [r for r in responses if r.status_code == 201]
    refused = [r for r in responses if r.status_code == 422]
    assert len(created) == len(refused) == PARALLEL // 2, [r.text for r in responses]
    assert len({r.json()["id"] for r in created}) == 1
    assert {r.json()["code"] for r in refused} == {"idempotency_key_reused"}
    assert await booked(live_db, room_type_id) + await booked(live_db, other_id) == 2


async def test_a_booking_is_pushed_to_dashboards_without_guest_data(
    test_settings: Settings,
    seeded: dict[str, int],
    live_db: async_sessionmaker[AsyncSession],
) -> None:
    room_type_id, guest_id = await one_room_left(live_db)
    async with live_clients(test_settings) as make:
        app, desk = await live(make)
        _, viewer = await make(app)
        await login(viewer, Roles.HOUSEKEEPING_SUPERVISOR)  # dashboard only
        async with connect(viewer) as ws:
            hello = await receive(ws)
            response = await post(desk, body(room_type_id, guest_id), uuid.uuid4().hex)
            assert response.status_code == 201, response.text

            changed = await receive(ws)
            summary = await receive(ws)

    assert changed["type"] == "reservation_changed"
    assert changed["seq"] == hello["seq"] + 1
    assert changed["payload"] == {
        "property_id": changed["payload"]["property_id"],
        "reservation_id": response.json()["id"],
        "change": "created",
        "status": "confirmed",
        "room_type_id": room_type_id,
        "check_in_date": TODAY.isoformat(),
        "check_out_date": (TODAY + timedelta(days=2)).isoformat(),
    }
    assert "Ada" not in str(changed)
    assert summary["type"] == "summary_changed"  # arriving today


async def test_swapping_room_types_in_parallel_does_not_deadlock(
    test_settings: Settings,
    seeded: dict[str, int],
    live_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A moves STD -> LST while B moves LST -> STD; each locks both types. A hook holds
    each request after its first room type lock until both have one (or 1 s passes),
    so in the wrong order they would cross and deadlock. In id order the second simply
    waits for the first."""
    lst_id, _ = await one_room_left(live_db)  # LST: two rooms, one booked tonight
    async with live_db() as db:
        pid = await get_default_property_id(db)
        std = await db.scalar(
            select(RoomType).where(RoomType.property_id == pid, RoomType.code == "STD")
        )
        assert std is not None
        lst = await db.get_one(RoomType, lst_id)
        later = (TODAY + timedelta(days=5), TODAY + timedelta(days=7))
        a = new_reservation(std, check_in_date=later[0], check_out_date=later[1])
        b = new_reservation(lst, check_in_date=later[0], check_out_date=later[1])
        db.add_all([a, b])
        await db.commit()
        moves = [(a.id, lst_id), (b.id, std.id)]

    holding: set[int] = set()
    both_hold = asyncio.Event()

    async def lock_and_wait(db: AsyncSession, property_id: int, room_type_id: int) -> RoomType:
        room_type = await lock_room_type(db, property_id, room_type_id)
        key = id(db)
        if key not in holding:  # first lock of this request
            holding.add(key)
            if len(holding) == 2:
                both_hold.set()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(both_hold.wait(), timeout=1)
        return room_type

    monkeypatch.setattr(reservation_module, "lock_room_type", lock_and_wait)
    async with live_clients(test_settings) as make:
        _, desk = await live(make)

        responses = await asyncio.wait_for(
            asyncio.gather(
                *(
                    desk.patch(
                        f"/api/reservations/{reservation_id}",
                        json={"version": 1, "room_type_id": target},
                    )
                    for reservation_id, target in moves
                )
            ),
            timeout=15,
        )

    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    async with live_db() as db:
        assert (await db.get_one(Reservation, moves[0][0])).room_type_id == lst_id
        assert (await db.get_one(Reservation, moves[1][0])).room_type_id == std.id
