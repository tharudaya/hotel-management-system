from datetime import date

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hotel_today
from app.core.config import Settings
from app.models import Guest, Reservation, ReservationStatus, Room, RoomType, UserRole
from tests.conftest import LoginAs


@pytest.fixture
async def reservation(db_session: AsyncSession) -> Reservation:
    std = RoomType(code="STD", name="Standard", max_occupancy=3)
    reservation = Reservation(
        confirmation_code="ABC123",
        guest=Guest(
            first_name="Ada", last_name="Lovelace", email="ada@example.com", phone="+1-555-0100"
        ),
        room_type=std,
        room=Room(number="304", floor=3, room_type=std),
        check_in_date=date(2026, 3, 8),
        check_out_date=date(2026, 3, 11),
        adults=2,
        children=1,
        status=ReservationStatus.CHECKED_IN,
        is_vip=True,
        notes="Late checkout requested",
    )
    db_session.add(reservation)
    await db_session.flush()
    return reservation


async def test_reservation_detail(
    client: AsyncClient, login_as: LoginAs, reservation: Reservation
) -> None:
    await login_as(UserRole.FRONT_DESK)

    response = await client.get(f"/api/reservations/{reservation.id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": reservation.id,
        "confirmation_code": "ABC123",
        "status": "checked_in",
        "check_in_date": "2026-03-08",
        "check_out_date": "2026-03-11",
        "nights": 3,
        "adults": 2,
        "children": 1,
        "is_vip": True,
        "notes": "Late checkout requested",
        "room_type": "STD",
        "room_type_name": "Standard",
        "room_id": reservation.room_id,
        "room_number": "304",
        "guest": {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "phone": "+1-555-0100",
        },
    }


async def test_reservation_detail_without_room(
    client: AsyncClient, login_as: LoginAs, reservation: Reservation, db_session: AsyncSession
) -> None:
    await login_as(UserRole.MANAGER)
    reservation.status = ReservationStatus.CONFIRMED
    reservation.room = None
    await db_session.flush()

    body = (await client.get(f"/api/reservations/{reservation.id}")).json()

    assert (body["room_id"], body["room_number"]) == (None, None)


async def test_reservation_detail_not_found(client: AsyncClient, login_as: LoginAs) -> None:
    await login_as(UserRole.FRONT_DESK)

    response = await client.get("/api/reservations/999999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Reservation 999999 not found", "code": "not_found"}


async def test_reservation_detail_forbidden_for_housekeeping(
    client: AsyncClient, login_as: LoginAs, reservation: Reservation
) -> None:
    await login_as(UserRole.HOUSEKEEPING)

    assert (await client.get(f"/api/reservations/{reservation.id}")).status_code == 403


async def test_reservation_detail_requires_login(
    client: AsyncClient, reservation: Reservation
) -> None:
    assert (await client.get(f"/api/reservations/{reservation.id}")).status_code == 401


# --- /api/hotel ------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(UserRole))
async def test_hotel_info_for_any_signed_in_user(
    client: AsyncClient, login_as: LoginAs, app: FastAPI, test_settings: Settings, role: UserRole
) -> None:
    app.dependency_overrides[get_hotel_today] = lambda: date(2026, 3, 10)
    await login_as(role)

    response = await client.get("/api/hotel")

    assert response.status_code == 200
    assert response.json() == {
        "name": test_settings.hotel_name,
        "timezone": "Europe/Stockholm",
        "business_date": "2026-03-10",
    }


async def test_hotel_info_requires_login(client: AsyncClient) -> None:
    assert (await client.get("/api/hotel")).status_code == 401
