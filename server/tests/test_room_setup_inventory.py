"""Room setup cannot take rooms from a room type that needs them for its bookings."""

from dataclasses import dataclass
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hotel_today
from app.models import HousekeepingStatus, ReservationStatus, Room, RoomType
from app.services.permissions import get_default_property_id
from tests.conftest import LoginAs, Roles
from tests.factories import new_reservation

TODAY: date = date(2026, 3, 10)
ROOMS = "/api/admin/rooms"


def day(offset: int) -> date:
    return TODAY + timedelta(days=offset)


@dataclass
class Hotel:
    std: RoomType  # rooms 101 and 102
    dlx: RoomType  # room 201
    rooms: dict[str, Room]


@pytest.fixture
async def hotel(db_session: AsyncSession, app: FastAPI, login_as: LoginAs) -> Hotel:
    app.dependency_overrides[get_hotel_today] = lambda: TODAY
    pid = await get_default_property_id(db_session)
    std = RoomType(property_id=pid, code="STD", name="Standard", max_occupancy=2)
    dlx = RoomType(property_id=pid, code="DLX", name="Deluxe", max_occupancy=2)
    rooms = {
        "101": Room(property_id=pid, number="101", floor=1, room_type=std),
        "102": Room(property_id=pid, number="102", floor=1, room_type=std),
        "201": Room(property_id=pid, number="201", floor=2, room_type=dlx),
    }
    db_session.add_all(rooms.values())
    await db_session.flush()
    await login_as(Roles.ADMIN)
    return Hotel(std=std, dlx=dlx, rooms=rooms)


async def book(
    db: AsyncSession,
    room_type: RoomType,
    check_in: int,
    check_out: int,
    status: ReservationStatus = ReservationStatus.CONFIRMED,
    room: Room | None = None,
) -> None:
    db.add(
        new_reservation(
            room_type,
            check_in_date=day(check_in),
            check_out_date=day(check_out),
            status=status,
            room=room,
        )
    )
    await db.flush()


async def room_entry(client: AsyncClient, number: str) -> dict[str, object]:
    rooms = (await client.get(ROOMS)).json()
    entry: dict[str, object] = next(r for r in rooms if r["number"] == number)
    return entry


FULL_ON_DAY_5 = (
    "Room type STD is fully booked on 2026-03-15; without room 101 it would be oversold. "
    "Move or cancel a STD booking for that night first."
)


async def test_retiring_a_room_the_bookings_need_is_refused(
    client: AsyncClient, db_session: AsyncSession, hotel: Hotel
) -> None:
    # Two unassigned bookings on day 5 need both Standard rooms; day 7 needs one.
    await book(db_session, hotel.std, 5, 6)
    await book(db_session, hotel.std, 5, 8)

    entry = await room_entry(client, "101")
    assert entry["retire_blocked"] == FULL_ON_DAY_5  # the hint, before trying
    response = await client.post(f"{ROOMS}/{hotel.rooms['101'].id}/retire", json={"version": 1})

    assert response.status_code == 409
    assert response.json() == {"detail": FULL_ON_DAY_5, "code": "would_oversell"}


async def test_retiring_a_spare_room_is_fine(
    client: AsyncClient, db_session: AsyncSession, hotel: Hotel
) -> None:
    await book(db_session, hotel.std, 5, 8)
    # Cancelled and checked-out stays need no room.
    await book(db_session, hotel.std, 5, 6, ReservationStatus.CANCELLED)
    await book(db_session, hotel.std, -2, 0, ReservationStatus.CHECKED_OUT, hotel.rooms["102"])

    assert (await room_entry(client, "101"))["retire_blocked"] is None
    response = await client.post(f"{ROOMS}/{hotel.rooms['101'].id}/retire", json={"version": 1})

    assert response.status_code == 200, response.text


async def test_past_bookings_do_not_block(
    client: AsyncClient, db_session: AsyncSession, hotel: Hotel
) -> None:
    await book(db_session, hotel.std, -5, -3)
    await book(db_session, hotel.std, -5, -3)

    response = await client.post(f"{ROOMS}/{hotel.rooms['101'].id}/retire", json={"version": 1})

    assert response.status_code == 200, response.text


async def test_changing_the_type_of_a_needed_room_is_refused(
    client: AsyncClient, db_session: AsyncSession, hotel: Hotel
) -> None:
    await book(db_session, hotel.std, 5, 6)
    await book(db_session, hotel.std, 5, 8)

    assert (await room_entry(client, "101"))["type_change_blocked"] == FULL_ON_DAY_5
    response = await client.patch(
        f"{ROOMS}/{hotel.rooms['101'].id}", json={"version": 1, "room_type_id": hotel.dlx.id}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "would_oversell"
    # Other edits of the same room are fine.
    renamed = await client.patch(
        f"{ROOMS}/{hotel.rooms['101'].id}", json={"version": 1, "floor": 3}
    )
    assert renamed.status_code == 200, renamed.text


async def test_moving_a_room_to_a_type_that_needs_it_is_fine(
    client: AsyncClient, db_session: AsyncSession, hotel: Hotel
) -> None:
    # Deluxe is full on day 5 (its one room); Standard has a spare room.
    await book(db_session, hotel.dlx, 5, 6)
    await book(db_session, hotel.std, 5, 6)

    response = await client.patch(
        f"{ROOMS}/{hotel.rooms['102'].id}", json={"version": 1, "room_type_id": hotel.dlx.id}
    )

    assert response.status_code == 200, response.text


async def test_overstaying_guest_counts_tonight(
    client: AsyncClient, db_session: AsyncSession, hotel: Hotel
) -> None:
    # Due out yesterday, still in room 102; an unassigned arrival tonight needs 101.
    await book(db_session, hotel.std, -3, -1, ReservationStatus.CHECKED_IN, hotel.rooms["102"])
    await book(db_session, hotel.std, 0, 1)

    response = await client.post(f"{ROOMS}/{hotel.rooms['101'].id}/retire", json={"version": 1})

    assert response.status_code == 409
    assert "fully booked on 2026-03-10" in response.json()["detail"]


async def test_an_out_of_order_room_can_go_even_when_tonight_is_full(
    client: AsyncClient, db_session: AsyncSession, hotel: Hotel
) -> None:
    # 102 is out of order, so tonight only 101 is sellable, and it is sold. Retiring
    # 102 changes nothing tonight; from tomorrow one room is enough.
    hotel.rooms["102"].housekeeping_status = HousekeepingStatus.OUT_OF_ORDER
    await book(db_session, hotel.std, 0, 2)
    version = (await room_entry(client, "102"))["version"]

    response = await client.post(
        f"{ROOMS}/{hotel.rooms['102'].id}/retire", json={"version": version}
    )

    assert response.status_code == 200, response.text


async def test_already_oversold_types_block_further_removals(
    client: AsyncClient, db_session: AsyncSession, hotel: Hotel
) -> None:
    for _ in range(3):  # 3 bookings, 2 rooms
        await book(db_session, hotel.std, 2, 3)

    response = await client.post(f"{ROOMS}/{hotel.rooms['101'].id}/retire", json={"version": 1})

    assert response.status_code == 409
    assert "fully booked on 2026-03-12" in response.json()["detail"]
