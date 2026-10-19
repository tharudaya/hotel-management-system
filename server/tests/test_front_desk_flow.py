"""Reservations meet the front desk: assign, check in, extend, check out, walk-ins, and the
dashboard counts after each step (which must agree with the availability service)."""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hotel_today
from app.models import (
    AuditLog,
    Guest,
    HousekeepingStatus,
    Reservation,
    ReservationStatus,
    Room,
    RoomType,
)
from app.realtime.events import DomainEvent, ReservationChanged, RoomStatusChanged, SummaryChanged
from app.services.permissions import get_default_property_id
from tests.conftest import LoginAs, Roles
from tests.factories import new_reservation

TODAY: date = date(2026, 3, 10)
RS = ReservationStatus
HK = HousekeepingStatus
RATE = 100_000


def day(offset: int) -> date:
    return TODAY + timedelta(days=offset)


@dataclass
class Hotel:
    std: RoomType  # rooms 101 (inspected), 102 (clean), 103 (dirty)
    dlx: RoomType  # room 201 (inspected)
    rooms: dict[str, Room]


@pytest.fixture
def published(app: FastAPI) -> list[DomainEvent]:
    events: list[DomainEvent] = []

    async def record(event: DomainEvent) -> None:
        events.append(event)

    app.state.event_bus.subscribe(record)
    return events


@pytest.fixture
async def hotel(db_session: AsyncSession, app: FastAPI) -> Hotel:
    app.dependency_overrides[get_hotel_today] = lambda: TODAY
    pid = await get_default_property_id(db_session)
    std = RoomType(
        property_id=pid, code="STD", name="Standard", max_occupancy=2, base_rate_minor=RATE
    )
    dlx = RoomType(
        property_id=pid, code="DLX", name="Deluxe", max_occupancy=3, base_rate_minor=RATE
    )
    rooms = {
        number: Room(
            property_id=pid,
            number=number,
            floor=int(number[0]),
            room_type=room_type,
            housekeeping_status=hk,
        )
        for number, room_type, hk in [
            ("101", std, HK.INSPECTED),
            ("102", std, HK.CLEAN),
            ("103", std, HK.DIRTY),
            ("201", dlx, HK.INSPECTED),
        ]
    }
    db_session.add_all(rooms.values())
    await db_session.flush()
    return Hotel(std=std, dlx=dlx, rooms=rooms)


async def stay(
    db: AsyncSession,
    room_type: RoomType,
    check_in: int,
    check_out: int,
    status: ReservationStatus = RS.CONFIRMED,
    room: Room | None = None,
    first_name: str = "Ada",
) -> Reservation:
    reservation = new_reservation(
        room_type,
        check_in_date=day(check_in),
        check_out_date=day(check_out),
        status=status,
        room=room,
        first_name=first_name,
        rate_minor=RATE,
    )
    db.add(reservation)
    await db.flush()
    return reservation


async def summary(client: AsyncClient) -> dict[str, Any]:
    response = await client.get("/api/dashboard/summary")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    # The dashboard and the availability service agree on tonight.
    availability = await client.get(
        "/api/reservations/availability",
        params={"check_in": str(day(0)), "check_out": str(day(1))},
    )
    assert body["available_tonight"] == sum(
        t["available"] for t in availability.json()["room_types"]
    )
    return body


async def tile(client: AsyncClient, number: str) -> dict[str, Any]:
    rooms = (await client.get("/api/dashboard/rooms")).json()["rooms"]
    found: dict[str, Any] = next(r for r in rooms if r["number"] == number)
    return found


def counts(body: dict[str, Any]) -> dict[str, int]:
    keys = (
        "arrivals_today",
        "arrivals_checked_in",
        "departures_today",
        "in_house",
        "available_tonight",
    )
    return {k: body[k] for k in keys}


async def book(client: AsyncClient, hotel: Hotel, **overrides: object) -> Response:
    body: dict[str, object] = {
        "guest": {"first_name": "Ada", "last_name": "Lovelace"},
        "room_type_id": hotel.std.id,
        "check_in_date": str(day(0)),
        "check_out_date": str(day(2)),
        "source": "phone",
    }
    body.update(overrides)
    return await client.post(
        "/api/reservations", json=body, headers={"Idempotency-Key": uuid.uuid4().hex}
    )


# --- the whole stay ----------------------------------------------------------------------


async def test_full_stay_from_booking_to_check_out(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, published: list[DomainEvent]
) -> None:
    await login_as(Roles.RECEPTIONIST)
    assert counts(await summary(client)) == {
        "arrivals_today": 0,
        "arrivals_checked_in": 0,
        "departures_today": 0,
        "in_house": 0,
        "available_tonight": 4,  # 101, 102, 103 (dirty rooms are sold too) and 201
    }

    # Book for tonight: an arrival without a room yet.
    created = await book(client, hotel)
    assert created.status_code == 201, created.text
    reservation = created.json()
    rid = reservation["id"]
    body = await summary(client)
    assert (body["arrivals_today"], body["available_tonight"]) == (1, 3)
    assert [(a["guest_name"], a["room_number"]) for a in body["arrivals"]] == [
        ("Ada Lovelace", None)
    ]

    # Assign room 101: its tile shows the arrival with the guest's name.
    published.clear()
    assigned = await client.post(
        f"/api/reservations/{rid}/assign-room",
        json={"version": 1, "room_id": hotel.rooms["101"].id},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["room_number"] == "101"
    entry = await tile(client, "101")
    assert (entry["occupancy_status"], entry["guest_name"], entry["nights_remaining"]) == (
        "arriving_today",
        "Ada Lovelace",
        2,
    )
    assert [type(e) for e in published] == [ReservationChanged, RoomStatusChanged, SummaryChanged]
    assert (await summary(client))["arrivals"][0]["room_number"] == "101"

    # Check in (version of the room tile, which the assignment moved on).
    checked_in = await client.post(
        f"/api/reservations/{rid}/check-in",
        json={"room_id": hotel.rooms["101"].id, "room_version": entry["version"]},
    )
    assert checked_in.status_code == 200, checked_in.text
    assert checked_in.json()["occupancy_status"] == "occupied"
    assert counts(await summary(client)) == {
        "arrivals_today": 1,
        "arrivals_checked_in": 1,
        "departures_today": 0,
        "in_house": 1,
        "available_tonight": 3,
    }

    # Extend by a night: the tile counts three nights now.
    detail = (await client.get(f"/api/reservations/{rid}")).json()
    extended = await client.patch(
        f"/api/reservations/{rid}",
        json={"version": detail["version"], "check_out_date": str(day(3))},
    )
    assert extended.status_code == 200, extended.text
    assert extended.json()["total_minor"] == 3 * RATE
    entry = await tile(client, "101")
    assert entry["nights_remaining"] == 3

    # Leave today, the day of arrival: one night is kept and charged.
    checked_out = await client.post(
        f"/api/reservations/{rid}/check-out", json={"room_version": entry["version"]}
    )
    assert checked_out.status_code == 200, checked_out.text
    assert (checked_out.json()["occupancy_status"], checked_out.json()["housekeeping_status"]) == (
        "vacant",
        "dirty",
    )
    final = (await client.get(f"/api/reservations/{rid}")).json()
    assert (final["status"], final["check_out_date"], final["total_minor"]) == (
        "checked_out",
        str(day(1)),
        RATE,
    )
    assert [h["action"] for h in final["history"]] == [
        "reservation.created",
        "reservation.room_assigned",
        "reservation.checked_in",
        "reservation.updated",
        "reservation.checked_out",
    ]
    assert counts(await summary(client)) == {
        "arrivals_today": 1,
        "arrivals_checked_in": 1,
        "departures_today": 1,  # came and went today (one night kept, so due tomorrow)
        "in_house": 0,
        "available_tonight": 4,
    }


async def test_early_departure_after_some_nights(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, -2, 3, RS.CHECKED_IN, room=hotel.rooms["101"])

    response = await client.post(f"/api/reservations/{r.id}/check-out", json={"room_version": 1})

    assert response.status_code == 200, response.text
    await db_session.refresh(r)
    assert (r.check_out_date, r.total_minor) == (TODAY, 2 * RATE)


async def test_overstay_check_out_keeps_the_booked_dates(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, -3, -1, RS.CHECKED_IN, room=hotel.rooms["101"])

    response = await client.post(f"/api/reservations/{r.id}/check-out", json={"room_version": 1})

    assert response.status_code == 200
    await db_session.refresh(r)
    assert (r.check_out_date, r.total_minor) == (day(-1), 2 * RATE)


# --- check-in rules ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("room", "check_in", "code"),
    [
        ("201", 0, "room_type_mismatch"),
        ("103", 0, "room_not_ready"),
        ("101", 1, "too_early"),
    ],
)
async def test_check_in_refusals(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    room: str,
    check_in: int,
    code: str,
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, check_in, check_in + 2)

    response = await client.post(
        f"/api/reservations/{r.id}/check-in",
        json={"room_id": hotel.rooms[room].id, "room_version": 1},
    )

    assert response.status_code == 409
    assert response.json()["code"] == code
    await db_session.refresh(r)
    assert r.status is RS.CONFIRMED


async def test_late_arrival_can_check_in_and_shows_on_the_dashboard(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, -1, 2, room=hotel.rooms["101"], first_name="Late")

    entry = await tile(client, "101")
    assert (entry["occupancy_status"], entry["guest_name"], entry["nights_remaining"]) == (
        "arriving_today",
        "Late Guest",
        2,
    )
    assert [a["guest_name"] for a in (await summary(client))["arrivals"]] == ["Late Guest"]

    response = await client.post(
        f"/api/reservations/{r.id}/check-in",
        json={"room_id": hotel.rooms["101"].id, "room_version": 1},
    )
    assert response.status_code == 200, response.text


# --- room assignment ---------------------------------------------------------------------


async def assign(client: AsyncClient, r: Reservation, room: Room, version: int = 1) -> Response:
    return await client.post(
        f"/api/reservations/{r.id}/assign-room", json={"version": version, "room_id": room.id}
    )


async def test_assign_ahead_and_move(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    published: list[DomainEvent],
) -> None:
    user = await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 0, 2)

    # A dirty room can be assigned (it will be cleaned before check-in).
    first = await assign(client, r, hotel.rooms["103"])
    assert first.status_code == 200, first.text
    published.clear()
    moved = await assign(client, r, hotel.rooms["101"], version=2)

    assert moved.status_code == 200, moved.text
    assert moved.json()["room_number"] == "101"
    assert {e.room.number for e in published if isinstance(e, RoomStatusChanged)} == {"101", "103"}
    assert (await tile(client, "103"))["occupancy_status"] == "vacant"
    audit = await db_session.scalar(
        select(AuditLog)
        .where(AuditLog.action == "reservation.room_assigned")
        .order_by(AuditLog.id.desc())
    )
    assert audit is not None and audit.actor_user_id == user.id
    assert (audit.before, audit.after) == (
        {"room_id": hotel.rooms["103"].id, "room_number": "103"},
        {
            "room_id": hotel.rooms["101"].id,
            "room_number": "101",
            "confirmation_code": r.confirmation_code,
        },
    )


async def test_unassign(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 0, 2, room=hotel.rooms["101"])

    response = await client.post(f"/api/reservations/{r.id}/unassign-room", json={"version": 1})

    assert response.status_code == 200, response.text
    assert response.json()["room_id"] is None
    assert (await tile(client, "101"))["occupancy_status"] == "vacant"


async def test_assign_refusals(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 5)
    await stay(db_session, hotel.std, 4, 6, room=hotel.rooms["102"])  # next guest in 102
    hotel.rooms["103"].is_active = False
    hotel.rooms["103"].retired_at = func.now()
    await db_session.flush()

    wrong_type = await assign(client, r, hotel.rooms["201"])
    taken = await assign(client, r, hotel.rooms["102"])
    retired = await assign(client, r, hotel.rooms["103"])
    stale = await assign(client, r, hotel.rooms["101"], version=7)

    assert (wrong_type.status_code, wrong_type.json()["code"]) == (409, "room_type_mismatch")
    assert (taken.status_code, taken.json()["code"]) == (409, "room_not_available")
    assert (retired.status_code, retired.json()["code"]) == (409, "invalid_transition")
    assert (stale.status_code, stale.json()["code"]) == (409, "version_conflict")
    await db_session.refresh(r)
    assert (r.room_id, r.version) == (None, 1)


async def test_turnover_room_can_be_assigned_but_not_an_overstayers(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    await stay(db_session, hotel.std, -2, 0, RS.CHECKED_IN, room=hotel.rooms["101"])  # leaves today
    await stay(db_session, hotel.std, -3, -1, RS.CHECKED_IN, room=hotel.rooms["102"])  # overstays
    r = await stay(db_session, hotel.std, 0, 2)

    assert (await assign(client, r, hotel.rooms["102"])).json()["code"] == "room_not_available"
    turnover = await assign(client, r, hotel.rooms["101"])
    assert turnover.status_code == 200, turnover.text
    # ...but the guest can only check in once the room is free and clean.
    entry = await tile(client, "101")
    assert (entry["occupancy_status"], entry["has_arrival_today"]) == ("departing_today", True)
    refused = await client.post(
        f"/api/reservations/{r.id}/check-in",
        json={"room_id": hotel.rooms["101"].id, "room_version": entry["version"]},
    )
    assert refused.json()["code"] == "room_not_vacant"


@pytest.mark.parametrize("status", [RS.CHECKED_IN, RS.CANCELLED])
async def test_only_confirmed_bookings_get_rooms_assigned(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    status: ReservationStatus,
) -> None:
    await login_as(Roles.RECEPTIONIST)
    room = hotel.rooms["101"] if status is RS.CHECKED_IN else None
    r = await stay(db_session, hotel.std, 0, 2, status, room=room)

    for response in [
        await assign(client, r, hotel.rooms["102"]),
        await client.post(f"/api/reservations/{r.id}/unassign-room", json={"version": 1}),
    ]:
        assert (response.status_code, response.json()["code"]) == (409, "invalid_transition")


@pytest.mark.parametrize(
    ("role", "status"),
    [
        (Roles.RECEPTIONIST, 200),
        (Roles.FRONT_OFFICE_MANAGER, 200),
        (Roles.HOUSEKEEPING_SUPERVISOR, 403),
    ],
)
async def test_assign_needs_check_in_permission(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    role: str,
    status: int,
) -> None:
    await login_as(role)
    r = await stay(db_session, hotel.std, 0, 2)

    assert (await assign(client, r, hotel.rooms["101"])).status_code == status


# --- walk-in -----------------------------------------------------------------------------


def walk_in_body(hotel: Hotel, *, room: str = "101", **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "guest": {"first_name": "Wally", "last_name": "Walker", "phone": "+46 70 555 01 01"},
        "room_id": hotel.rooms[room].id,
        "room_version": 1,
        "check_out_date": str(day(2)),
        "adults": 2,
    }
    body.update(overrides)
    return body


async def walk_in(client: AsyncClient, body: dict[str, object], key: str | None = None) -> Response:
    return await client.post(
        "/api/reservations/walk-in", json=body, headers={"Idempotency-Key": key or uuid.uuid4().hex}
    )


async def test_walk_in_books_and_checks_in_at_once(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    published: list[DomainEvent],
) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await walk_in(client, walk_in_body(hotel))

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["status"], body["source"], body["room_number"]) == ("checked_in", "walk_in", "101")
    assert (body["check_in_date"], body["nights"], body["total_minor"]) == (str(TODAY), 2, 2 * RATE)
    actions = (await db_session.scalars(select(AuditLog.action).order_by(AuditLog.id))).all()
    assert actions == ["guest.created", "reservation.created", "reservation.checked_in"]
    assert [type(e) for e in published] == [ReservationChanged, RoomStatusChanged, SummaryChanged]
    assert (await tile(client, "101"))["guest_name"] == "Wally Walker"
    assert counts(await summary(client)) == {
        "arrivals_today": 1,
        "arrivals_checked_in": 1,
        "departures_today": 0,
        "in_house": 1,
        "available_tonight": 3,
    }


@pytest.mark.parametrize(("room", "code"), [("103", "room_not_ready"), ("102", "room_not_vacant")])
async def test_failed_walk_in_leaves_nothing_behind(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    room: str,
    code: str,
) -> None:
    await stay(db_session, hotel.std, -1, 1, RS.CHECKED_IN, room=hotel.rooms["102"])
    await login_as(Roles.RECEPTIONIST)
    reservations_before = await db_session.scalar(select(func.count()).select_from(Reservation))
    guests_before = await db_session.scalar(select(func.count()).select_from(Guest))

    response = await walk_in(client, walk_in_body(hotel, room=room))

    assert (response.status_code, response.json()["code"]) == (409, code)
    assert (
        await db_session.scalar(select(func.count()).select_from(Reservation))
        == reservations_before
    )
    assert await db_session.scalar(select(func.count()).select_from(Guest)) == guests_before


async def test_walk_in_is_idempotent(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    key = uuid.uuid4().hex

    first = await walk_in(client, walk_in_body(hotel), key)
    again = await walk_in(client, walk_in_body(hotel), key)
    other = await walk_in(client, walk_in_body(hotel, adults=1), key)

    assert (first.status_code, again.status_code) == (201, 201)
    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json()["id"] == first.json()["id"]
    assert (other.status_code, other.json()["code"]) == (422, "idempotency_key_reused")


async def test_walk_in_needs_create_and_check_in(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel
) -> None:
    await login_as(Roles.HOUSEKEEPING_SUPERVISOR)

    assert (await walk_in(client, walk_in_body(hotel))).status_code == 403


@pytest.mark.parametrize(
    "overrides",
    [
        {"room_version": None},
        {"check_out_date": "2026-03-10"},  # no night
        {"guest": None},
        {"check_in_date": "2026-03-10"},  # always today: not accepted
    ],
)
async def test_walk_in_validation(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, overrides: dict[str, object]
) -> None:
    await login_as(Roles.RECEPTIONIST)
    body = walk_in_body(hotel) | overrides
    body = {k: v for k, v in body.items() if v is not None}

    assert (await walk_in(client, body)).status_code == 422
