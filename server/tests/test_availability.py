"""Availability per room type: counting, validation and the API."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import Connection, event
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hotel_today
from app.models import HousekeepingStatus, Reservation, ReservationStatus, Room, RoomType
from app.services.availability import AvailabilityService
from app.services.errors import InvalidInputError
from app.services.permissions import get_default_property_id
from tests.conftest import LoginAs, Roles
from tests.factories import new_reservation

TODAY: date = date(2026, 3, 10)
RS = ReservationStatus


def day(offset: int) -> date:
    return TODAY + timedelta(days=offset)


@dataclass
class Hotel:
    property_id: int
    std: RoomType  # 3 active rooms (101-103) and a retired one (104), up to 2 guests
    dlx: RoomType  # 2 rooms (201, 202), up to 3 guests
    rooms: dict[str, Room]


@pytest.fixture
async def hotel(db_session: AsyncSession) -> Hotel:
    pid = await get_default_property_id(db_session)
    std = RoomType(
        property_id=pid, code="STD", name="Standard", max_occupancy=2, base_rate_minor=100_000
    )
    dlx = RoomType(
        property_id=pid, code="DLX", name="Deluxe", max_occupancy=3, base_rate_minor=150_000
    )
    retired_type = RoomType(
        property_id=pid, code="OLD", name="Old wing", max_occupancy=4, is_active=False
    )
    rooms = {
        number: Room(property_id=pid, number=number, floor=int(number[0]), room_type=room_type)
        for number, room_type in [
            ("101", std),
            ("102", std),
            ("103", std),
            ("201", dlx),
            ("202", dlx),
        ]
    }
    rooms["104"] = Room(
        property_id=pid,
        number="104",
        floor=1,
        room_type=std,
        is_active=False,
        retired_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    rooms["901"] = Room(property_id=pid, number="901", floor=9, room_type=retired_type)
    db_session.add_all(rooms.values())
    await db_session.flush()
    return Hotel(property_id=pid, std=std, dlx=dlx, rooms=rooms)


async def book(
    db: AsyncSession,
    room_type: RoomType,
    check_in: int,
    check_out: int,
    status: ReservationStatus = RS.CONFIRMED,
    room: Room | None = None,
) -> Reservation:
    reservation = new_reservation(
        room_type,
        check_in_date=day(check_in),
        check_out_date=day(check_out),
        status=status,
        room=room,
    )
    db.add(reservation)
    await db.flush()
    return reservation


async def free(
    db: AsyncSession,
    hotel: Hotel,
    check_in: int,
    check_out: int,
    *,
    adults: int = 1,
    children: int = 0,
    exclude: int | None = None,
) -> dict[str, int]:
    """Available rooms per room type code."""
    result = await AvailabilityService(db, TODAY).available_by_room_type(
        hotel.property_id, day(check_in), day(check_out), adults, children, exclude
    )
    return {t.code: t.available for t in result.room_types}


# --- counting ----------------------------------------------------------------------------


async def test_empty_hotel_with_rates(db_session: AsyncSession, hotel: Hotel) -> None:
    result = await AvailabilityService(db_session, TODAY).available_by_room_type(
        hotel.property_id, day(0), day(3), 2, 0
    )

    assert (result.nights, result.currency) == (3, "SEK")
    # Active types only, by name; the retired type and the retired room do not count.
    assert [(t.code, t.available) for t in result.room_types] == [("DLX", 2), ("STD", 3)]
    std = result.room_types[1]
    assert [(r.night_date, r.rate_minor) for r in std.nightly_rates] == [
        (day(0), 100_000),
        (day(1), 100_000),
        (day(2), 100_000),
    ]
    assert (std.total_minor, std.currency, std.max_occupancy) == (300_000, "SEK", 2)


async def test_overlapping_stays_take_the_busiest_night(
    db_session: AsyncSession, hotel: Hotel
) -> None:
    await book(db_session, hotel.std, 0, 2)
    await book(db_session, hotel.std, 0, 2)
    await book(db_session, hotel.std, 1, 3)
    # Booked per night: day 0: 2, day 1: 3, day 2: 1, day 3: 0.

    assert (await free(db_session, hotel, 0, 1))["STD"] == 1
    assert (await free(db_session, hotel, 0, 3))["STD"] == 0
    assert (await free(db_session, hotel, 2, 4))["STD"] == 2
    assert (await free(db_session, hotel, 3, 5))["STD"] == 3
    assert (await free(db_session, hotel, 0, 3))["DLX"] == 2


async def test_back_to_back_stays_do_not_conflict(db_session: AsyncSession, hotel: Hotel) -> None:
    for _ in range(3):
        await book(db_session, hotel.std, 0, 2)  # full on days 0 and 1, leaving on day 2

    assert (await free(db_session, hotel, 2, 4))["STD"] == 3  # arriving on the check-out day
    assert (await free(db_session, hotel, 1, 3))["STD"] == 0


async def test_cancelled_no_show_and_checked_out_stays_hold_nothing(
    db_session: AsyncSession, hotel: Hotel
) -> None:
    await book(db_session, hotel.std, 0, 2, RS.CANCELLED)
    await book(db_session, hotel.std, 0, 2, RS.NO_SHOW)
    # Left early: the booked dates still cover tonight, but the room is free again.
    await book(db_session, hotel.std, -1, 2, RS.CHECKED_OUT, room=hotel.rooms["101"])

    assert (await free(db_session, hotel, 0, 2))["STD"] == 3


async def test_guests_in_house_count_including_overstays(
    db_session: AsyncSession, hotel: Hotel
) -> None:
    await book(db_session, hotel.std, -2, 1, RS.CHECKED_IN, room=hotel.rooms["101"])
    # Due out yesterday, still in house: holds a room tonight only.
    await book(db_session, hotel.std, -3, -1, RS.CHECKED_IN, room=hotel.rooms["102"])

    assert (await free(db_session, hotel, 0, 1))["STD"] == 1
    assert (await free(db_session, hotel, 1, 2))["STD"] == 3


async def test_guests_due_out_today_do_not_hold_tonight(
    db_session: AsyncSession, hotel: Hotel
) -> None:
    for number in ("101", "102", "103"):
        await book(db_session, hotel.std, -2, 0, RS.CHECKED_IN, room=hotel.rooms[number])

    assert (await free(db_session, hotel, 0, 1))["STD"] == 3


async def test_retired_rooms_and_room_types_are_not_sold(
    db_session: AsyncSession, hotel: Hotel
) -> None:
    room = hotel.rooms["103"]
    room.is_active = False
    room.retired_at = datetime(2026, 3, 1, tzinfo=UTC)
    await db_session.flush()

    assert await free(db_session, hotel, 0, 2) == {"DLX": 2, "STD": 2}


async def test_out_of_order_rooms_are_unavailable_tonight_only(
    db_session: AsyncSession, hotel: Hotel
) -> None:
    hotel.rooms["102"].housekeeping_status = HousekeepingStatus.OUT_OF_ORDER
    await db_session.flush()

    assert (await free(db_session, hotel, 0, 1))["STD"] == 2
    assert (await free(db_session, hotel, 0, 3))["STD"] == 2
    assert (await free(db_session, hotel, 1, 3))["STD"] == 3


async def test_out_of_order_room_with_a_guest_in_house_is_counted_once(
    db_session: AsyncSession, hotel: Hotel
) -> None:
    hotel.rooms["102"].housekeeping_status = HousekeepingStatus.OUT_OF_ORDER
    await book(db_session, hotel.std, -1, 2, RS.CHECKED_IN, room=hotel.rooms["102"])

    assert (await free(db_session, hotel, 0, 1))["STD"] == 2


async def test_room_types_too_small_for_the_party_are_left_out(
    db_session: AsyncSession, hotel: Hotel
) -> None:
    assert set(await free(db_session, hotel, 0, 1, adults=2)) == {"DLX", "STD"}
    assert set(await free(db_session, hotel, 0, 1, adults=2, children=1)) == {"DLX"}
    assert await free(db_session, hotel, 0, 1, adults=3, children=1) == {}


async def test_exclude_reservation_ignores_the_booking_being_changed(
    db_session: AsyncSession, hotel: Hotel
) -> None:
    mine = await book(db_session, hotel.std, 0, 2)
    await book(db_session, hotel.std, 0, 2)
    await book(db_session, hotel.std, 1, 3)

    assert (await free(db_session, hotel, 0, 3))["STD"] == 0
    assert (await free(db_session, hotel, 0, 3, exclude=mine.id))["STD"] == 1


async def test_oversold_type_shows_zero(db_session: AsyncSession, hotel: Hotel) -> None:
    for _ in range(4):  # more than the 3 rooms (e.g. a room was retired later)
        await book(db_session, hotel.std, 0, 1)

    assert (await free(db_session, hotel, 0, 1))["STD"] == 0


@pytest.fixture
async def statements(db_session: AsyncSession) -> AsyncIterator[list[str]]:
    executed: list[str] = []
    sync_conn = (await db_session.connection()).sync_connection
    assert sync_conn is not None

    def record(
        conn: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        executed.append(statement)

    event.listen(sync_conn, "before_cursor_execute", record)
    yield executed
    event.remove(sync_conn, "before_cursor_execute", record)


async def test_counts_in_one_query_whatever_the_length(
    db_session: AsyncSession, hotel: Hotel, statements: list[str]
) -> None:
    await book(db_session, hotel.std, 0, 5)
    statements.clear()

    await free(db_session, hotel, 0, 30)

    # The property (unless already loaded) and one availability query; no per-night queries.
    assert len([s for s in statements if "reservations" in s]) == 1
    assert len(statements) <= 2


# --- validation --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("check_in", "check_out", "adults", "children", "code"),
    [
        (-1, 1, 1, 0, "check_in_in_past"),
        (0, 31, 1, 0, "stay_too_long"),
        (731, 732, 1, 0, "beyond_booking_horizon"),
        (2, 2, 1, 0, "invalid_dates"),
        (3, 2, 1, 0, "invalid_dates"),
        (0, 1, 0, 0, "invalid_guests"),
        (0, 1, 1, -1, "invalid_guests"),
    ],
)
async def test_stays_the_hotel_does_not_take(
    db_session: AsyncSession,
    hotel: Hotel,
    check_in: int,
    check_out: int,
    adults: int,
    children: int,
    code: str,
) -> None:
    with pytest.raises(InvalidInputError) as raised:
        await free(db_session, hotel, check_in, check_out, adults=adults, children=children)

    assert raised.value.code == code


async def test_limits_are_inclusive(db_session: AsyncSession, hotel: Hotel) -> None:
    assert await free(db_session, hotel, 0, 30)  # max_stay_nights = 30
    assert await free(db_session, hotel, 730, 731)  # booking_horizon_days = 730


# --- API ---------------------------------------------------------------------------------


@pytest.fixture
def fixed_today(app: FastAPI) -> None:
    app.dependency_overrides[get_hotel_today] = lambda: TODAY


def url(check_in: int, check_out: int, adults: int = 1, children: int = 0) -> str:
    return (
        f"/api/reservations/availability?check_in={day(check_in)}&check_out={day(check_out)}"
        f"&adults={adults}&children={children}"
    )


async def test_availability_api(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    fixed_today: None,
    db_session: AsyncSession,
) -> None:
    await book(db_session, hotel.std, 0, 2)
    await login_as(Roles.RECEPTIONIST)

    response = await client.get(url(0, 2, adults=2))

    assert response.status_code == 200, response.text
    body = response.json()
    assert {k: body[k] for k in ("check_in_date", "check_out_date", "nights", "currency")} == {
        "check_in_date": "2026-03-10",
        "check_out_date": "2026-03-12",
        "nights": 2,
        "currency": "SEK",
    }
    std = next(t for t in body["room_types"] if t["code"] == "STD")
    assert std == {
        "room_type_id": hotel.std.id,
        "code": "STD",
        "name": "Standard",
        "max_occupancy": 2,
        "bed_setup": None,
        "available": 2,
        "nightly_rates": [
            {"night_date": "2026-03-10", "rate_minor": 100_000},
            {"night_date": "2026-03-11", "rate_minor": 100_000},
        ],
        "total_minor": 200_000,
        "currency": "SEK",
    }


async def test_availability_api_refuses_past_stays(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, fixed_today: None
) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await client.get(url(-1, 1))

    assert response.status_code == 422
    assert response.json()["code"] == "check_in_in_past"


@pytest.mark.parametrize(
    "query",
    [
        "",
        "check_in=2026-03-10",
        "check_in=2026-03-10&check_out=tomorrow",
        "check_in=2026-03-10&check_out=2026-03-11&adults=0",
        "check_in=2026-03-10&check_out=2026-03-11&children=-1",
        "check_in=2026-03-10&check_out=2026-03-11&adults=13",
        "check_in=2026-03-10&check_out=2026-03-11&pets=1",
    ],
)
async def test_availability_api_validates_parameters(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, fixed_today: None, query: str
) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await client.get(f"/api/reservations/availability?{query}")

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (Roles.RECEPTIONIST, True),
        (Roles.FRONT_OFFICE_MANAGER, True),
        (Roles.GENERAL_MANAGER, True),
        (Roles.HOUSEKEEPING_SUPERVISOR, False),
        (Roles.ROOM_ATTENDANT, False),
    ],
)
async def test_availability_needs_reservation_create(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    fixed_today: None,
    role: str,
    allowed: bool,
) -> None:
    await login_as(role)

    response = await client.get(url(0, 1))

    assert response.status_code == (200 if allowed else 403)


async def test_availability_requires_login(client: AsyncClient) -> None:
    assert (await client.get(url(0, 1))).status_code == 401
