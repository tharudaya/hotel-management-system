from collections.abc import AsyncIterator
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import Connection, event
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hotel_today
from app.core.clock import hotel_today
from app.core.config import Settings
from app.models import (
    Guest,
    HousekeepingStatus,
    Reservation,
    ReservationStatus,
    Room,
    RoomType,
)
from app.services.dashboard import get_dashboard_rooms, get_dashboard_summary
from app.services.permissions import get_default_property_id
from tests.conftest import LoginAs, Roles

ROOMS: str = "/api/dashboard/rooms"
SUMMARY: str = "/api/dashboard/summary"
TODAY: date = date(2026, 3, 10)
HK = HousekeepingStatus
RS = ReservationStatus


@pytest.fixture
def fixed_today(app: FastAPI) -> date:
    app.dependency_overrides[get_hotel_today] = lambda: TODAY
    return TODAY


@pytest.fixture
async def hotel(db_session: AsyncSession) -> dict[str, Room]:
    """A small hotel covering every dashboard case. Returns rooms by number."""
    pid = await get_default_property_id(db_session)
    std = RoomType(property_id=pid, code="STD", name="Standard", max_occupancy=3)
    housekeeping = {
        "101": HK.INSPECTED,  # vacant
        "102": HK.CLEAN,  # occupied (VIP, notes)
        "103": HK.DIRTY,  # departing today
        "104": HK.CLEAN,  # arriving today
        "105": HK.DIRTY,  # departure + arrival today
        "106": HK.OUT_OF_ORDER,
        "107": HK.CLEAN,  # overstay: was due out yesterday
        "108": HK.DIRTY,  # checked out earlier today -> vacant
        "201": HK.INSPECTED,  # arrival in the future -> vacant
        "202": HK.IN_PROGRESS,  # arrived and checked in today
        "203": HK.OUT_OF_ORDER,  # out of order but has an arrival assigned
    }
    rooms = {
        number: Room(
            property_id=pid,
            number=number,
            floor=int(number[0]),
            room_type=std,
            housekeeping_status=hk,
        )
        for number, hk in housekeeping.items()
    }
    db_session.add_all(rooms.values())

    def book(
        room: str | None,
        first_name: str,
        check_in: int,
        check_out: int,
        status: ReservationStatus,
        *,
        is_vip: bool = False,
        notes: str | None = None,
        adults: int = 1,
        children: int = 0,
    ) -> None:
        db_session.add(
            Reservation(
                confirmation_code=f"C-{first_name}",
                guest=Guest(first_name=first_name, last_name="Guest"),
                room_type=std,
                room=rooms[room] if room else None,
                check_in_date=TODAY + timedelta(days=check_in),
                check_out_date=TODAY + timedelta(days=check_out),
                status=status,
                is_vip=is_vip,
                notes=notes,
                adults=adults,
                children=children,
            )
        )

    book(
        "102", "Vip", -2, 3, RS.CHECKED_IN, is_vip=True, notes="Late checkout", adults=2, children=1
    )
    book("103", "Dora", -1, 0, RS.CHECKED_IN)
    book("104", "Arnie", 0, 2, RS.CONFIRMED)
    book("105", "Leaver", -3, 0, RS.CHECKED_IN)
    book("105", "Comer", 0, 1, RS.CONFIRMED)
    book("107", "Stayer", -3, -1, RS.CHECKED_IN)
    book("108", "Gone", -2, 0, RS.CHECKED_OUT)
    book("201", "Later", 3, 5, RS.CONFIRMED)
    book("202", "Early", 0, 2, RS.CHECKED_IN)
    book("203", "Blocked", 0, 2, RS.CONFIRMED)
    book(None, "Unassigned", 0, 1, RS.CONFIRMED)
    book(None, "Cancelled", 0, 2, RS.CANCELLED)
    book(None, "Noshow", -1, 1, RS.NO_SHOW)
    await db_session.flush()
    return rooms


# --- GET /api/dashboard/rooms ----------------------------------------------------


async def test_rooms_derives_status_for_each_case(
    client: AsyncClient, login_as: LoginAs, hotel: dict[str, Room], fixed_today: date
) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await client.get(ROOMS)

    assert response.status_code == 200
    body = response.json()
    assert body["business_date"] == "2026-03-10"
    by_number = {room["number"]: room for room in body["rooms"]}
    assert list(by_number) == sorted(hotel), "ordered by floor, then number"

    def summary(number: str) -> tuple[str, str | None, int | None, bool]:
        r = by_number[number]
        return (
            r["occupancy_status"],
            r["guest_name"],
            r["nights_remaining"],
            r["has_arrival_today"],
        )

    assert summary("101") == ("vacant", None, None, False)
    assert summary("102") == ("occupied", "Vip Guest", 3, False)
    assert summary("103") == ("departing_today", "Dora Guest", 0, False)
    assert summary("104") == ("arriving_today", "Arnie Guest", 2, True)
    assert summary("105") == ("departing_today", "Leaver Guest", 0, True)
    assert summary("106") == ("out_of_order", None, None, False)
    assert summary("107") == ("departing_today", "Stayer Guest", 0, False)
    assert summary("108") == ("vacant", None, None, False)
    assert summary("201") == ("vacant", None, None, False)
    assert summary("202") == ("occupied", "Early Guest", 2, False)
    assert summary("203") == ("out_of_order", None, None, True)


async def test_rooms_entry_shape(
    client: AsyncClient, login_as: LoginAs, hotel: dict[str, Room], fixed_today: date
) -> None:
    await login_as(Roles.RECEPTIONIST)

    body = (await client.get(ROOMS)).json()

    vip_room = next(r for r in body["rooms"] if r["number"] == "102")
    assert vip_room == {
        "room_id": hotel["102"].id,
        "number": "102",
        "floor": 1,
        "room_type": "STD",
        "room_type_name": "Standard",
        "housekeeping_status": "clean",
        "occupancy_status": "occupied",
        "guest_name": "Vip Guest",
        "reservation_id": vip_room["reservation_id"],
        "nights_remaining": 3,
        "is_vip": True,
        "has_notes": True,
        "has_arrival_today": False,
        "version": 1,
    }
    assert isinstance(vip_room["reservation_id"], int)
    vacant = next(r for r in body["rooms"] if r["number"] == "101")
    assert (vacant["reservation_id"], vacant["is_vip"], vacant["has_notes"]) == (None, False, False)


async def test_rooms_empty_hotel(client: AsyncClient, login_as: LoginAs, fixed_today: date) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await client.get(ROOMS)

    assert response.status_code == 200
    assert response.json() == {"business_date": "2026-03-10", "rooms": []}


# --- GET /api/dashboard/summary --------------------------------------------------


async def test_summary_counts(
    client: AsyncClient, login_as: LoginAs, hotel: dict[str, Room], fixed_today: date
) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await client.get(SUMMARY)

    assert response.status_code == 200
    assert response.json() == {
        "business_date": "2026-03-10",
        # 104, 105 (Comer), 202 (already in), 203, unassigned; cancelled excluded
        "arrivals_today": 5,
        "arrivals_checked_in": 1,
        # 103, 105 (Leaver), 107 (overstay), 108 (already out)
        "departures_today": 4,
        "departures_checked_out": 1,
        # 102, 103, 105, 107, 202
        "in_house": 5,
        "in_house_guests": 3 + 1 + 1 + 1 + 1,
        # 11 rooms - 2 out of order = 9 sellable; sold tonight: 102, 202 staying
        # + 104, 105, 203, unassigned arriving = 6
        "available_tonight": 3,
        "dirty": 3,
        "out_of_order": 2,
        "occupancy_percent": 66.7,
    }


async def test_summary_empty_hotel(
    client: AsyncClient, login_as: LoginAs, fixed_today: date
) -> None:
    await login_as(Roles.RECEPTIONIST)

    body = (await client.get(SUMMARY)).json()

    assert body["occupancy_percent"] == 0.0
    assert body["available_tonight"] == 0


async def test_business_date_uses_hotel_time_zone(
    client: AsyncClient, login_as: LoginAs, test_settings: Settings
) -> None:
    await login_as(Roles.RECEPTIONIST)

    body = (await client.get(SUMMARY)).json()

    assert body["business_date"] == hotel_today(test_settings.hotel_tz).isoformat()


# --- query count (no N+1) --------------------------------------------------------


@pytest.fixture
async def statements(db_session: AsyncSession) -> AsyncIterator[list[str]]:
    """SQL statements executed on the test connection while the fixture is active."""
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


async def test_rooms_query_count_is_constant(
    db_session: AsyncSession, hotel: dict[str, Room], statements: list[str]
) -> None:
    result = await get_dashboard_rooms(db_session, TODAY)

    assert len(result.rooms) == len(hotel)
    assert len(statements) == 2


async def test_summary_query_count_is_constant(
    db_session: AsyncSession, hotel: dict[str, Room], statements: list[str]
) -> None:
    await get_dashboard_summary(db_session, TODAY)

    assert len(statements) == 2


# --- permissions -----------------------------------------------------------------


@pytest.mark.parametrize("path", [ROOMS, SUMMARY])
@pytest.mark.parametrize("role", [Roles.RECEPTIONIST, Roles.GENERAL_MANAGER, Roles.ADMIN])
async def test_allowed_roles(
    client: AsyncClient, login_as: LoginAs, fixed_today: date, path: str, role: str
) -> None:
    await login_as(role)

    assert (await client.get(path)).status_code == 200


@pytest.mark.parametrize("path", [ROOMS, SUMMARY])
async def test_housekeeping_forbidden(client: AsyncClient, login_as: LoginAs, path: str) -> None:
    await login_as(Roles.ROOM_ATTENDANT)

    response = await client.get(path)

    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient permissions"}


@pytest.mark.parametrize("path", [ROOMS, SUMMARY])
async def test_requires_login(client: AsyncClient, path: str) -> None:
    assert (await client.get(path)).status_code == 401
