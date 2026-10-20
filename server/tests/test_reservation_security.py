"""Reservations and guests: other properties' data is invisible, contact details follow
guest.view_contact, and input is length-limited.

Permissions per endpoint are covered for every operation by test_security.py and
test_permission_enforcement.py; this file covers what depends on data.
"""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hotel_today
from app.core.permissions import ALL_PERMISSION_KEYS, P
from app.models import (
    HousekeepingStatus,
    Property,
    Reservation,
    ReservationStatus,
    Role,
    RolePermission,
    Room,
    RoomType,
    UserPropertyRole,
)
from app.services.permissions import get_default_property_id
from tests.conftest import LoginAs, MakeUser, Roles
from tests.factories import new_guest, new_reservation

TODAY: date = date(2026, 3, 10)
EMAIL = "secret.guest@example.com"
PHONE = "+46701112233"


def day(offset: int) -> date:
    return TODAY + timedelta(days=offset)


@dataclass
class Theirs:
    """Another property's room type, room, guest and bookings."""

    room_type: RoomType
    room: Room
    guest_id: int
    confirmed: Reservation
    in_house: Reservation


@dataclass
class Ours:
    room_type: RoomType
    room: Room
    confirmed: Reservation


@pytest.fixture
async def data(db_session: AsyncSession, app: FastAPI) -> tuple[Ours, Theirs]:
    app.dependency_overrides[get_hotel_today] = lambda: TODAY
    pid = await get_default_property_id(db_session)
    other = Property(name="Other Hotel", time_zone="UTC")
    db_session.add(other)
    await db_session.flush()

    def hotel(property_id: int, prefix: str) -> tuple[RoomType, Room, Room]:
        room_type = RoomType(
            property_id=property_id,
            code="STD",
            name="Standard",
            max_occupancy=2,
            base_rate_minor=100_000,
        )
        rooms = [
            Room(
                property_id=property_id,
                number=f"{prefix}{n}",
                floor=1,
                room_type=room_type,
                housekeeping_status=HousekeepingStatus.INSPECTED,
            )
            for n in (1, 2)
        ]
        return room_type, rooms[0], rooms[1]

    our_type, our_room, our_spare_room = hotel(pid, "1")
    their_type, their_room, their_other_room = hotel(other.id, "9")
    their_guest = new_guest(other.id, "Theo", "Theirs", email=EMAIL, phone=PHONE)
    ours = new_reservation(
        our_type,
        check_in_date=day(0),
        check_out_date=day(2),
        guest=new_guest(pid, "Olga", "Ours", email=EMAIL, phone=PHONE),
    )
    their_confirmed = new_reservation(
        their_type, check_in_date=day(0), check_out_date=day(2), guest=their_guest
    )
    their_in_house = new_reservation(
        their_type,
        check_in_date=day(-1),
        check_out_date=day(1),
        status=ReservationStatus.CHECKED_IN,
        room=their_other_room,
        guest=their_guest,
    )
    db_session.add_all(
        [our_room, our_spare_room, their_room, ours, their_confirmed, their_in_house]
    )
    await db_session.flush()
    return (
        Ours(room_type=our_type, room=our_room, confirmed=ours),
        Theirs(
            room_type=their_type,
            room=their_room,
            guest_id=their_guest.id,
            confirmed=their_confirmed,
            in_house=their_in_house,
        ),
    )


# --- other properties ---------------------------------------------------------------------


async def test_another_propertys_bookings_rooms_and_guests_are_not_found(
    client: AsyncClient, login_as: LoginAs, data: tuple[Ours, Theirs]
) -> None:
    ours, theirs = data
    await login_as(Roles.GENERAL_MANAGER)
    rid, code = theirs.confirmed.id, theirs.confirmed.confirmation_code
    key = {"Idempotency-Key": uuid.uuid4().hex}
    stay = {"check_in_date": str(day(0)), "check_out_date": str(day(1)), "source": "phone"}

    attempts = {
        "detail": await client.get(f"/api/reservations/{rid}"),
        "by code": await client.get(f"/api/reservations/by-code/{code}"),
        "change": await client.patch(f"/api/reservations/{rid}", json={"version": 1, "notes": "x"}),
        "preview": await client.post(
            f"/api/reservations/{rid}/preview-change", json={"version": 1, "notes": "x"}
        ),
        "cancel": await client.post(
            f"/api/reservations/{rid}/cancel", json={"version": 1, "reason": "x"}
        ),
        "no-show": await client.post(f"/api/reservations/{rid}/no-show", json={"version": 1}),
        "assign their room": await client.post(
            f"/api/reservations/{ours.confirmed.id}/assign-room",
            json={"version": 1, "room_id": theirs.room.id},
        ),
        "assign to their booking": await client.post(
            f"/api/reservations/{rid}/assign-room", json={"version": 1, "room_id": ours.room.id}
        ),
        "unassign": await client.post(
            f"/api/reservations/{rid}/unassign-room", json={"version": 1}
        ),
        "check in their booking": await client.post(
            f"/api/reservations/{rid}/check-in",
            json={"room_id": ours.room.id, "room_version": 1},
        ),
        "check in to their room": await client.post(
            f"/api/reservations/{ours.confirmed.id}/check-in",
            json={"room_id": theirs.room.id, "room_version": 1},
        ),
        "check out": await client.post(
            f"/api/reservations/{theirs.in_house.id}/check-out", json={"room_version": 1}
        ),
        "housekeeping": await client.patch(
            f"/api/rooms/{theirs.room.id}/housekeeping-status",
            json={"status": "dirty", "version": 1},
        ),
        "guest": await client.get(f"/api/guests/{theirs.guest_id}"),
        "edit guest": await client.patch(
            f"/api/guests/{theirs.guest_id}", json={"first_name": "X"}
        ),
        "book their type": await client.post(
            "/api/reservations",
            headers=key,
            json={
                **stay,
                "guest": {"first_name": "A", "last_name": "B"},
                "room_type_id": theirs.room_type.id,
            },
        ),
        "walk-in to their room": await client.post(
            "/api/reservations/walk-in",
            headers={"Idempotency-Key": uuid.uuid4().hex},
            json={
                "guest": {"first_name": "A", "last_name": "B"},
                "room_id": theirs.room.id,
                "room_version": 1,
                "check_out_date": str(day(1)),
            },
        ),
    }

    assert {name: r.status_code for name, r in attempts.items()} == dict.fromkeys(attempts, 404)
    # A guest of theirs is not a guest here either.
    for_their_guest = await client.post(
        "/api/reservations",
        headers={"Idempotency-Key": uuid.uuid4().hex},
        json={**stay, "guest_id": theirs.guest_id, "room_type_id": ours.room_type.id},
    )
    assert (for_their_guest.status_code, for_their_guest.json()["code"]) == (422, "guest_not_found")


async def test_lists_and_dashboard_show_only_this_property(
    client: AsyncClient, login_as: LoginAs, data: tuple[Ours, Theirs]
) -> None:
    _, theirs = data
    await login_as(Roles.GENERAL_MANAGER)

    listed = (await client.get("/api/reservations")).json()
    guests = (await client.get("/api/guests", params={"q": "theirs"})).json()
    rooms = (await client.get("/api/dashboard/rooms")).json()["rooms"]
    summary = (await client.get("/api/dashboard/summary")).json()
    availability = (
        await client.get(
            "/api/reservations/availability",
            params={"check_in": str(day(0)), "check_out": str(day(1))},
        )
    ).json()

    assert [i["guest_name"] for i in listed["items"]] == ["Olga Ours"]
    assert guests["items"] == []
    assert {r["number"] for r in rooms} == {"11", "12"}
    assert summary["in_house"] == 0
    assert [a["guest_name"] for a in summary["arrivals"]] == ["Olga Ours"]
    assert [t["room_type_id"] for t in availability["room_types"]] != [theirs.room_type.id]
    assert len(availability["room_types"]) == 1


# --- contact details ----------------------------------------------------------------------


async def login_without_contact(client: AsyncClient, make_user: MakeUser, db: AsyncSession) -> None:
    """Every permission except guest.view_contact."""
    pid = await get_default_property_id(db)
    role = Role(property_id=pid, name="Everything but contact")
    db.add(role)
    await db.flush()
    db.add_all(
        RolePermission(role_id=role.id, permission_key=key)
        for key in ALL_PERMISSION_KEYS - {P.GUEST_VIEW_CONTACT}
    )
    user = await make_user("nocontact", role=None)
    db.add(UserPropertyRole(user_id=user.id, property_id=pid, role_id=role.id))
    await db.flush()
    response = await client.post(
        "/api/auth/login", json={"username": "nocontact", "password": "nocontact-password"}
    )
    assert response.status_code == 200


async def test_contact_details_never_reach_users_without_guest_view_contact(
    client: AsyncClient,
    make_user: MakeUser,
    db_session: AsyncSession,
    data: tuple[Ours, Theirs],
) -> None:
    ours, _ = data
    await login_without_contact(client, make_user, db_session)
    rid, code = ours.confirmed.id, ours.confirmed.confirmation_code
    # Their own changes: a new guest and a booking for them.
    created = await client.post(
        "/api/reservations",
        headers={"Idempotency-Key": uuid.uuid4().hex},
        json={
            "guest": {"first_name": "Nina", "last_name": "New", "email": EMAIL, "phone": PHONE},
            "room_type_id": ours.room_type.id,
            "check_in_date": str(day(0)),
            "check_out_date": str(day(1)),
            "source": "phone",
        },
    )
    assert created.status_code == 201
    guest_id = created.json()["guest"]["id"]

    responses = [
        created,
        await client.get(f"/api/reservations/{rid}"),
        await client.get(f"/api/reservations/by-code/{code}"),
        await client.get("/api/reservations"),
        await client.get("/api/guests", params={"q": "olga"}),
        await client.get(f"/api/guests/{guest_id}"),
        await client.patch(f"/api/guests/{guest_id}", json={"notes": "Quiet room"}),
        await client.post(
            f"/api/reservations/{rid}/preview-change", json={"version": 1, "notes": "x"}
        ),
        await client.patch(f"/api/reservations/{rid}", json={"version": 1, "notes": "x"}),
        await client.get("/api/dashboard/rooms"),
        await client.get("/api/dashboard/summary"),
        await client.get("/api/admin/audit"),
        # Error answers too: searching by contact details finds nothing and says nothing.
        await client.get("/api/guests", params={"q": EMAIL}),
        await client.get("/api/guests", params={"q": PHONE}),
        await client.patch(f"/api/guests/{guest_id}", json={"email": "not an email"}),
    ]

    for response in responses:
        assert response.status_code < 500, response.text
        body = response.text
        assert EMAIL not in body and PHONE not in body and "701112233" not in body, (
            response.request.url,
            body[:300],
        )


async def test_contact_details_shown_with_guest_view_contact(
    client: AsyncClient, login_as: LoginAs, data: tuple[Ours, Theirs]
) -> None:
    ours, _ = data
    await login_as(Roles.FRONT_OFFICE_MANAGER)

    guest = (await client.get(f"/api/reservations/{ours.confirmed.id}")).json()["guest"]

    assert (guest["email"], guest["phone"], guest["contact_hidden"]) == (EMAIL, PHONE, False)


# --- input limits -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/guests", {"first_name": "x" * 101, "last_name": "y"}),
        ("POST", "/api/guests", {"first_name": "x", "last_name": "y", "notes": "n" * 2001}),
        ("POST", "/api/guests", {"first_name": "x", "last_name": "y", "email": "a" * 320 + "@b.c"}),
        ("POST", "/api/guests", {"first_name": "x", "last_name": "y", "phone": "1" * 51}),
        ("POST", "/api/guests", {"first_name": "x", "last_name": "y", "colour": "red"}),
        ("CANCEL", "", {"version": 1, "reason": "r" * 501}),
        ("NO_SHOW", "", {"version": 1, "reason": "r" * 501}),
        ("PATCH", "", {"version": 1, "notes": "n" * 2001}),
        ("PATCH", "", {"version": 1, "adults": 13}),
        ("PATCH", "", {"version": 1, "nightly_rate_minor": 100_000_001}),
        ("PATCH", "", {"version": 0, "notes": "x"}),
    ],
)
async def test_input_is_length_and_range_limited(
    client: AsyncClient,
    login_as: LoginAs,
    data: tuple[Ours, Theirs],
    method: str,
    path: str,
    body: dict[str, object],
) -> None:
    ours, _ = data
    await login_as(Roles.GENERAL_MANAGER)
    rid = ours.confirmed.id
    if method == "CANCEL":
        response = await client.post(f"/api/reservations/{rid}/cancel", json=body)
    elif method == "NO_SHOW":
        response = await client.post(f"/api/reservations/{rid}/no-show", json=body)
    elif method == "PATCH":
        response = await client.patch(f"/api/reservations/{rid}", json=body)
    else:
        response = await client.request(method, path, json=body)

    assert response.status_code == 422, response.text


async def test_idempotency_keys_and_searches_are_limited(
    client: AsyncClient, login_as: LoginAs, data: tuple[Ours, Theirs]
) -> None:
    await login_as(Roles.GENERAL_MANAGER)

    long_key = await client.post(
        "/api/reservations", headers={"Idempotency-Key": "k" * 101}, json={}
    )
    long_search = await client.get("/api/reservations", params={"q": "q" * 101})
    long_guest_search = await client.get("/api/guests", params={"q": "q" * 101})

    assert [long_key.status_code, long_search.status_code, long_guest_search.status_code] == [
        422,
        422,
        422,
    ]
