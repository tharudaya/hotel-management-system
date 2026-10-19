"""Room setup meets the front desk: one story through the real APIs.

An admin sets up room types and rooms; a receptionist uses the dashboard and checks a
guest in and out; the room setup rules hold at every point, and the dashboard and
its counts follow every change.
"""

from datetime import date, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hotel_today
from app.models import ReservationStatus
from app.services.permissions import get_default_property_id
from tests.conftest import MakeUser, Roles
from tests.factories import new_reservation

TODAY = date(2026, 3, 10)


@pytest.fixture(autouse=True)
def fixed_today(app: FastAPI) -> None:
    app.dependency_overrides[get_hotel_today] = lambda: TODAY


async def sign_in(client: AsyncClient, make_user: MakeUser, username: str, role: str) -> None:
    await make_user(username, role=role)
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": f"{username}-password"}
    )
    assert response.status_code == 200, response.text


async def dashboard(desk: AsyncClient) -> dict[str, dict[str, Any]]:
    rooms = (await desk.get("/api/dashboard/rooms")).json()["rooms"]
    return {r["number"]: r for r in rooms}


async def summary(desk: AsyncClient) -> dict[str, Any]:
    data: dict[str, Any] = (await desk.get("/api/dashboard/summary")).json()
    return data


async def test_room_setup_and_the_front_desk(
    client: AsyncClient,
    second_client: AsyncClient,
    make_user: MakeUser,
    db_session: AsyncSession,
) -> None:
    admin, desk = client, second_client
    await sign_in(admin, make_user, "boss", Roles.ADMIN)
    await sign_in(desk, make_user, "desk", Roles.RECEPTIONIST)

    # --- the admin sets up two room types and three rooms --------------------------
    std = (
        await admin.post(
            "/api/admin/room-types", json={"code": "STD", "name": "Standard", "max_occupancy": 2}
        )
    ).json()
    dlx = (
        await admin.post(
            "/api/admin/room-types", json={"code": "DLX", "name": "Deluxe", "max_occupancy": 3}
        )
    ).json()
    rooms = {}
    for number, room_type in (("101", std), ("102", std), ("201", dlx)):
        response = await admin.post(
            "/api/admin/rooms",
            json={"number": number, "floor": int(number[0]), "room_type_id": room_type["id"]},
        )
        assert response.status_code == 201, response.text
        rooms[number] = response.json()

    # New rooms are on the dashboard at once, ready to sell, and counted.
    tiles = await dashboard(desk)
    assert set(tiles) == {"101", "102", "201"}
    assert {t["housekeeping_status"] for t in tiles.values()} == {"inspected"}
    assert (await summary(desk))["available_tonight"] == 3
    # The dashboard's room type filter is built from these tiles: both types.
    assert {t["room_type"] for t in tiles.values()} == {"STD", "DLX"}

    # --- a guest arrives for a Standard room ---------------------------------------
    stay = new_reservation(
        std["id"],
        property_id=await get_default_property_id(db_session),
        check_in_date=TODAY,
        check_out_date=TODAY + timedelta(days=2),
    )
    db_session.add(stay)
    await db_session.flush()

    # Check-in only works for an active room of the reserved type.
    wrong_type = await desk.post(
        f"/api/reservations/{stay.id}/check-in",
        json={"room_id": rooms["201"]["id"], "room_version": tiles["201"]["version"]},
    )
    assert wrong_type.status_code == 409
    assert wrong_type.json()["code"] == "room_type_mismatch"

    retired = await admin.post(
        f"/api/admin/rooms/{rooms['102']['id']}/retire", json={"version": rooms["102"]["version"]}
    )
    assert retired.status_code == 200
    refused = await desk.post(
        f"/api/reservations/{stay.id}/check-in",
        json={"room_id": rooms["102"]["id"], "room_version": retired.json()["version"]},
    )
    assert refused.status_code == 409
    assert refused.json() == {"detail": "Room 102 is retired.", "code": "invalid_transition"}

    # The retired room has left the dashboard and every count.
    tiles = await dashboard(desk)
    assert set(tiles) == {"101", "201"}
    assert (await summary(desk))["available_tonight"] == 1  # 2 rooms, 1 sold tonight

    checked_in = await desk.post(
        f"/api/reservations/{stay.id}/check-in",
        json={"room_id": rooms["101"]["id"], "room_version": tiles["101"]["version"]},
    )
    assert checked_in.status_code == 200, checked_in.text

    # --- with a guest in house, the admin is held back ------------------------------
    room_101 = next(r for r in (await admin.get("/api/admin/rooms")).json() if r["number"] == "101")
    assert room_101["guest_in_house"] is True
    for response, message in (
        (
            await admin.post(
                f"/api/admin/rooms/{room_101['id']}/retire", json={"version": room_101["version"]}
            ),
            "Room 101 has a guest checked in.",
        ),
        (
            await admin.patch(
                f"/api/admin/rooms/{room_101['id']}",
                json={"version": room_101["version"], "room_type_id": dlx["id"]},
            ),
            "Room 101 has a guest checked in, so its room type cannot change. "
            "Try again after check-out.",
        ),
        (
            await admin.post(f"/api/admin/room-types/{std['id']}/retire"),
            "Room type STD still has 1 active room. Retire them or give them another type first.",
        ),
    ):
        assert response.status_code == 409
        assert response.json()["detail"] == message

    # Renaming a type shows on the dashboard straight away.
    await admin.patch(f"/api/admin/room-types/{std['id']}", json={"name": "Standard Queen"})
    assert (await dashboard(desk))["101"]["room_type_name"] == "Standard Queen"

    # --- after check-out, Standard can be wound down --------------------------------
    tiles = await dashboard(desk)
    checked_out = await desk.post(
        f"/api/reservations/{stay.id}/check-out",
        json={"room_version": tiles["101"]["version"]},
    )
    assert checked_out.status_code == 200, checked_out.text
    room_101 = next(r for r in (await admin.get("/api/admin/rooms")).json() if r["number"] == "101")
    assert (
        await admin.post(
            f"/api/admin/rooms/{room_101['id']}/retire", json={"version": room_101["version"]}
        )
    ).status_code == 200
    assert (await admin.post(f"/api/admin/room-types/{std['id']}/retire")).status_code == 200

    # Only the Deluxe room is left; the retired type is gone from the dashboard, so it
    # is gone from its filter options too.
    tiles = await dashboard(desk)
    assert set(tiles) == {"201"}
    assert {t["room_type"] for t in tiles.values()} == {"DLX"}
    counts = await summary(desk)
    assert (counts["available_tonight"], counts["dirty"], counts["out_of_order"]) == (1, 0, 0)

    # A room of a retired type cannot come back on its own.
    reactivate = await admin.post(
        f"/api/admin/rooms/{room_101['id']}/reactivate",
        json={"version": room_101["version"] + 1},
    )
    assert reactivate.status_code == 409
    assert reactivate.json()["code"] == "room_type_retired"


async def test_upcoming_reservation_type_cannot_be_retired_and_keeps_a_room(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    """A type with bookings ahead stays, so arriving guests always have a sellable type."""
    await sign_in(client, make_user, "boss", Roles.ADMIN)
    fam = (
        await client.post(
            "/api/admin/room-types", json={"code": "FAM", "name": "Family", "max_occupancy": 4}
        )
    ).json()
    db_session.add(
        new_reservation(
            fam["id"],
            property_id=await get_default_property_id(db_session),
            first_name="Bo",
            check_in_date=TODAY + timedelta(days=30),
            check_out_date=TODAY + timedelta(days=33),
            status=ReservationStatus.CONFIRMED,
        )
    )
    await db_session.flush()

    response = await client.post(f"/api/admin/room-types/{fam['id']}/retire")

    assert response.status_code == 409
    assert response.json()["detail"] == "Room type FAM has 1 upcoming reservation."
