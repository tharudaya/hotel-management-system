from datetime import date

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hotel_today
from app.core.config import Settings
from app.core.permissions import P
from app.models import (
    Reservation,
    ReservationStatus,
    Role,
    RolePermission,
    Room,
    RoomType,
    UserPropertyRole,
)
from app.services.permissions import get_default_property_id
from tests.conftest import USERNAME_FOR_ROLE, LoginAs, MakeUser, Roles
from tests.factories import new_guest, new_reservation


@pytest.fixture
async def reservation(db_session: AsyncSession) -> Reservation:
    pid = await get_default_property_id(db_session)
    std = RoomType(property_id=pid, code="STD", name="Standard", max_occupancy=3)
    reservation = new_reservation(
        std,
        confirmation_code="ABC23456",
        guest=new_guest(pid, "Ada", "Lovelace", email="ada@example.com", phone="+1-555-0100"),
        room=Room(property_id=pid, number="304", floor=3, room_type=std),
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
    await login_as(Roles.RECEPTIONIST)

    response = await client.get(f"/api/reservations/{reservation.id}")

    assert response.status_code == 200
    body = response.json()
    assert body.pop("created_at")
    assert body == {
        "id": reservation.id,
        "confirmation_code": "ABC23456",
        "status": "checked_in",
        "source": "phone",
        "check_in_date": "2026-03-08",
        "check_out_date": "2026-03-11",
        "nights": 3,
        "adults": 2,
        "children": 1,
        "is_vip": True,
        "notes": "Late checkout requested",
        "room_type_id": reservation.room_type_id,
        "room_type": "STD",
        "room_type_name": "Standard",
        "room_id": reservation.room_id,
        "room_number": "304",
        "guest": {
            "id": reservation.guest_id,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "phone": "+1-555-0100",
            "contact_hidden": False,
        },
        "nightly_rates": [
            {"night_date": "2026-03-08", "rate_minor": 100_000},
            {"night_date": "2026-03-09", "rate_minor": 100_000},
            {"night_date": "2026-03-10", "rate_minor": 100_000},
        ],
        "total_minor": 300_000,
        "currency": "SEK",
        "version": 1,
    }


async def test_reservation_detail_without_room(
    client: AsyncClient, login_as: LoginAs, reservation: Reservation, db_session: AsyncSession
) -> None:
    await login_as(Roles.GENERAL_MANAGER)
    reservation.status = ReservationStatus.CONFIRMED
    reservation.checked_in_at = None
    reservation.room = None
    await db_session.flush()

    body = (await client.get(f"/api/reservations/{reservation.id}")).json()

    assert (body["room_id"], body["room_number"]) == (None, None)


async def test_reservation_detail_not_found(client: AsyncClient, login_as: LoginAs) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await client.get("/api/reservations/999999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Reservation 999999 not found", "code": "not_found"}


async def test_reservation_detail_forbidden_for_housekeeping(
    client: AsyncClient, login_as: LoginAs, reservation: Reservation
) -> None:
    await login_as(Roles.ROOM_ATTENDANT)

    assert (await client.get(f"/api/reservations/{reservation.id}")).status_code == 403


async def test_reservation_detail_requires_login(
    client: AsyncClient, reservation: Reservation
) -> None:
    assert (await client.get(f"/api/reservations/{reservation.id}")).status_code == 401


# --- /api/hotel ------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(USERNAME_FOR_ROLE))
async def test_hotel_info_for_any_signed_in_user(
    client: AsyncClient, login_as: LoginAs, app: FastAPI, test_settings: Settings, role: str
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


async def test_contact_details_need_guest_view_contact(
    client: AsyncClient,
    make_user: MakeUser,
    reservation: Reservation,
    db_session: AsyncSession,
) -> None:
    # A custom role that may view reservations but not guests' contact details.
    property_id = await get_default_property_id(db_session)
    role = Role(property_id=property_id, name="Night porter")
    db_session.add(role)
    await db_session.flush()
    db_session.add(RolePermission(role_id=role.id, permission_key=P.RESERVATION_VIEW))
    user = await make_user("porter", role=None)
    db_session.add(UserPropertyRole(user_id=user.id, property_id=property_id, role_id=role.id))
    await db_session.flush()
    await client.post("/api/auth/login", json={"username": "porter", "password": "porter-password"})

    guest = (await client.get(f"/api/reservations/{reservation.id}")).json()["guest"]

    assert guest == {
        "id": reservation.guest_id,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": None,
        "phone": None,
        "contact_hidden": True,
    }
