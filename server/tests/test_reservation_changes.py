"""Changing, cancelling and no-shows: transitions, prices, rooms, availability, history."""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hotel_today
from app.models import (
    AuditLog,
    Property,
    Reservation,
    ReservationNight,
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
OLD_RATE = 90_000  # what the fixtures' bookings were sold at
STD_RATE = 100_000  # the Standard base rate now
DLX_RATE = 150_000


def day(offset: int) -> date:
    return TODAY + timedelta(days=offset)


@dataclass
class Hotel:
    property_id: int
    std: RoomType  # rooms 101, 102; up to 2 guests
    dlx: RoomType  # room 201; up to 3 guests
    rooms: dict[str, Room]


@pytest.fixture
def fixed_today(app: FastAPI) -> None:
    app.dependency_overrides[get_hotel_today] = lambda: TODAY


@pytest.fixture
def published(app: FastAPI) -> list[DomainEvent]:
    events: list[DomainEvent] = []

    async def record(event: DomainEvent) -> None:
        events.append(event)

    app.state.event_bus.subscribe(record)
    return events


@pytest.fixture
async def hotel(db_session: AsyncSession, fixed_today: None) -> Hotel:
    pid = await get_default_property_id(db_session)
    std = RoomType(
        property_id=pid, code="STD", name="Standard", max_occupancy=2, base_rate_minor=STD_RATE
    )
    dlx = RoomType(
        property_id=pid, code="DLX", name="Deluxe", max_occupancy=3, base_rate_minor=DLX_RATE
    )
    rooms = {
        "101": Room(property_id=pid, number="101", floor=1, room_type=std),
        "102": Room(property_id=pid, number="102", floor=1, room_type=std),
        "201": Room(property_id=pid, number="201", floor=2, room_type=dlx),
    }
    db_session.add_all(rooms.values())
    await db_session.flush()
    return Hotel(property_id=pid, std=std, dlx=dlx, rooms=rooms)


async def stay(
    db: AsyncSession,
    room_type: RoomType,
    check_in: int,
    check_out: int,
    status: ReservationStatus = RS.CONFIRMED,
    room: Room | None = None,
    *,
    confirmation_code: str | None = None,
    notes: str | None = None,
) -> Reservation:
    reservation = new_reservation(
        room_type,
        check_in_date=day(check_in),
        check_out_date=day(check_out),
        status=status,
        room=room,
        rate_minor=OLD_RATE,
        confirmation_code=confirmation_code,
        notes=notes,
    )
    db.add(reservation)
    await db.flush()
    return reservation


async def change(client: AsyncClient, r: Reservation, version: int = 1, **body: object) -> Response:
    return await client.patch(f"/api/reservations/{r.id}", json={"version": version, **body})


async def cancel(
    client: AsyncClient, r: Reservation, reason: str | None = "Plans changed", version: int = 1
) -> Response:
    body: dict[str, Any] = {"version": version}
    if reason is not None:
        body["reason"] = reason
    return await client.post(f"/api/reservations/{r.id}/cancel", json=body)


async def no_show(client: AsyncClient, r: Reservation, version: int = 1) -> Response:
    return await client.post(f"/api/reservations/{r.id}/no-show", json={"version": version})


async def fresh(db: AsyncSession, r: Reservation) -> Reservation:
    await db.refresh(r)
    return r


async def nights_of(db: AsyncSession, r: Reservation) -> dict[date, int]:
    rows = await db.execute(
        select(ReservationNight.night_date, ReservationNight.rate_minor).where(
            ReservationNight.reservation_id == r.id
        )
    )
    return {key: value for key, value in rows}


async def audits(db: AsyncSession, action: str) -> list[AuditLog]:
    return list((await db.scalars(select(AuditLog).where(AuditLog.action == action))).all())


def rates(body: dict[str, Any]) -> list[tuple[str, int]]:
    return [(n["night_date"], n["rate_minor"]) for n in body["nightly_rates"]]


# --- modify: confirmed -------------------------------------------------------------------


async def test_extend_keeps_old_prices_and_charges_new_nights_at_the_base_rate(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    published: list[DomainEvent],
) -> None:
    user = await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 4)

    response = await change(client, r, check_out_date=str(day(6)))

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["check_out_date"], body["nights"], body["version"]) == ("2026-03-16", 4, 2)
    assert rates(body) == [
        ("2026-03-12", OLD_RATE),
        ("2026-03-13", OLD_RATE),
        ("2026-03-14", STD_RATE),
        ("2026-03-15", STD_RATE),
    ]
    assert body["total_minor"] == 2 * OLD_RATE + 2 * STD_RATE
    [audit] = await audits(db_session, "reservation.updated")
    assert audit.actor_user_id == user.id
    assert audit.before is not None and audit.after is not None
    assert (audit.before["check_out_date"], audit.after["check_out_date"]) == (
        "2026-03-14",
        "2026-03-16",
    )
    assert (audit.before["total_minor"], audit.after["total_minor"]) == (
        2 * OLD_RATE,
        2 * OLD_RATE + 2 * STD_RATE,
    )
    [event] = published  # nothing on the dashboard today: no summary
    assert event == ReservationChanged(
        property_id=hotel.property_id,
        reservation_id=r.id,
        change="updated",
        status="confirmed",
        room_type_id=hotel.std.id,
        check_in_date=day(2),
        check_out_date=day(6),
    )
    [entry] = body["history"]
    assert (entry["action"], entry["actor"]["id"], entry["changed"]) == (
        "reservation.updated",
        user.id,
        ["check_out_date", "nightly_rates", "total_minor"],
    )


async def test_shortening_needs_no_availability(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 6)
    # The type is oversold on those nights (e.g. a room was retired outside the app).
    for _ in range(2):
        await stay(db_session, hotel.std, 2, 6)

    response = await change(client, r, check_out_date=str(day(3)))

    assert response.status_code == 200, response.text
    assert await nights_of(db_session, r) == {day(2): OLD_RATE}
    assert response.json()["total_minor"] == OLD_RATE


async def test_new_dates_that_are_full_leave_the_booking_unchanged(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 4)
    for _ in range(2):  # both Standard rooms taken on day 5
        await stay(db_session, hotel.std, 5, 6)

    response = await change(client, r, check_in_date=str(day(4)), check_out_date=str(day(6)))

    assert response.status_code == 409
    assert response.json()["code"] == "no_availability"
    assert response.json()["unavailable_nights"] == ["2026-03-15"]
    r = await fresh(db_session, r)
    assert (r.check_in_date, r.check_out_date, r.version) == (day(2), day(4), 1)
    assert await nights_of(db_session, r) == {day(2): OLD_RATE, day(3): OLD_RATE}
    assert await audits(db_session, "reservation.updated") == []


async def test_the_booking_does_not_compete_with_itself(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 4)
    await stay(db_session, hotel.std, 2, 5)  # with r, both rooms are taken on days 2-3

    # Moving one night later needs day 4, where one room is free; days 2-3 are its own.
    response = await change(client, r, check_in_date=str(day(3)), check_out_date=str(day(5)))

    assert response.status_code == 200, response.text


async def test_change_room_type(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    published: list[DomainEvent],
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 4)

    response = await change(client, r, room_type_id=hotel.dlx.id, adults=3)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["room_type"], body["adults"]) == ("DLX", 3)
    # Policy: nights that remain keep their price, even in another room type.
    assert rates(body) == [("2026-03-12", OLD_RATE), ("2026-03-13", OLD_RATE)]
    [audit] = await audits(db_session, "reservation.updated")
    assert audit.before is not None and audit.after is not None
    assert (audit.before["room_type_id"], audit.after["room_type_id"]) == (
        hotel.std.id,
        hotel.dlx.id,
    )
    assert isinstance(published[0], ReservationChanged)
    assert published[0].room_type_id == hotel.dlx.id


async def test_change_room_type_when_the_new_type_is_full(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 4)
    await stay(db_session, hotel.dlx, 3, 4)  # the only Deluxe room, day 3

    response = await change(client, r, room_type_id=hotel.dlx.id)

    assert response.status_code == 409
    assert response.json()["unavailable_nights"] == ["2026-03-13"]
    assert (await fresh(db_session, r)).room_type_id == hotel.std.id


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"adults": 2, "children": 1}, 422, "too_many_guests"),  # Standard sleeps 2
        ({"check_in_date": "2026-03-09"}, 422, "check_in_in_past"),
        ({"check_out_date": "2026-03-12"}, 422, "invalid_dates"),
        ({"check_out_date": "2026-04-12"}, 422, "stay_too_long"),
        ({"room_type_id": 999999}, 404, "room_type_not_found"),
    ],
)
async def test_changes_the_hotel_does_not_take(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    body: dict[str, Any],
    status: int,
    code: str,
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 4)

    response = await change(client, r, **body)

    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    assert (await fresh(db_session, r)).version == 1


async def test_change_to_a_retired_room_type(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    hotel.dlx.is_active = False
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 4)

    response = await change(client, r, room_type_id=hotel.dlx.id)

    assert response.status_code == 409
    assert response.json()["code"] == "room_type_retired"


async def test_late_arrival_can_still_change_departure(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    # Due yesterday, not here yet, not marked as a no-show.
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, -1, 2)

    response = await change(client, r, check_out_date=str(day(3)))

    assert response.status_code == 200, response.text


async def test_notes_and_vip_only(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 4, notes="Old note")

    response = await change(client, r, notes="  Cot needed ", is_vip=True)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["notes"], body["is_vip"], body["total_minor"]) == ("Cot needed", True, 180_000)
    cleared = await change(client, r, version=2, notes=None)
    assert cleared.json()["notes"] is None


async def test_unchanged_values_change_nothing(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    published: list[DomainEvent],
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 4)

    response = await change(client, r, check_in_date=str(day(2)), adults=1)

    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert published == []
    assert await audits(db_session, "reservation.updated") == []


@pytest.mark.parametrize(
    "body",
    [
        {"check_in_date": None},
        {"room_type_id": None},
        {"adults": 0},
        {"is_vip": None},
        {"status": "cancelled"},
        {"room_id": 1},
        {"notes": "x" * 2001},
    ],
)
async def test_modify_body_validation(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    body: dict[str, Any],
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 4)

    assert (await change(client, r, **body)).status_code == 422


async def test_version_is_required(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 4)

    response = await client.patch(f"/api/reservations/{r.id}", json={"notes": "x"})

    assert response.status_code == 422


async def test_stale_version_is_a_conflict(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.FRONT_OFFICE_MANAGER)
    r = await stay(db_session, hotel.std, 2, 4)
    assert (await change(client, r, notes="First")).status_code == 200

    for response in [
        await change(client, r, version=1, notes="Second"),
        await cancel(client, r, version=1),
    ]:
        assert response.status_code == 409
        assert response.json()["code"] == "version_conflict"
    r = await fresh(db_session, r)
    assert (r.notes, r.status, r.version) == ("First", RS.CONFIRMED, 2)


# --- modify: prices ----------------------------------------------------------------------


async def test_managers_can_reprice_every_night(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.FRONT_OFFICE_MANAGER)
    r = await stay(db_session, hotel.std, 2, 4)

    response = await change(client, r, check_out_date=str(day(5)), nightly_rate_minor=80_000)

    assert response.status_code == 200, response.text
    assert [rate for _, rate in rates(response.json())] == [80_000] * 3
    [audit] = await audits(db_session, "reservation.updated")
    assert audit.after is not None
    assert audit.after["total_minor"] == 240_000


@pytest.mark.parametrize(
    "body",
    [
        {"nightly_rate_minor": 80_000},
        {"check_out_date": "2026-03-15", "nightly_rate_minor": 90_000},  # new night below base
    ],
)
async def test_receptionist_cannot_change_prices(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    body: dict[str, Any],
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 4)

    response = await change(client, r, **body)

    assert response.status_code == 403
    assert response.json()["code"] == "rate_override_not_allowed"
    assert await nights_of(db_session, r) == {day(2): OLD_RATE, day(3): OLD_RATE}


async def test_sending_the_prices_it_already_has_is_not_a_change_of_price(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 4)

    response = await change(client, r, nightly_rate_minor=OLD_RATE)

    assert response.status_code == 200


async def test_new_nights_of_a_type_without_a_rate_need_a_price(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    hotel.std.base_rate_minor = 0
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 4)

    response = await change(client, r, check_out_date=str(day(5)))

    assert response.status_code == 409
    assert response.json()["code"] == "rate_not_set"


# --- modify: assigned rooms --------------------------------------------------------------


async def test_assigned_room_stays_when_still_free(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    published: list[DomainEvent],
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 0, 2, room=hotel.rooms["101"])

    response = await change(client, r, check_out_date=str(day(3)))

    assert response.status_code == 200, response.text
    assert response.json()["room_number"] == "101"
    # The arrival tile shows the new departure, and the room's version moved on.
    room_events = [e for e in published if isinstance(e, RoomStatusChanged)]
    assert [e.room.number for e in room_events] == ["101"]
    assert room_events[0].room.version == 2
    assert any(isinstance(e, SummaryChanged) for e in published)


async def test_assigned_room_is_released_when_taken_on_the_new_nights(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 4, room=hotel.rooms["101"])
    await stay(db_session, hotel.std, 5, 7, room=hotel.rooms["101"])

    response = await change(client, r, check_out_date=str(day(6)))

    assert response.status_code == 200, response.text
    assert (response.json()["room_id"], response.json()["room_number"]) == (None, None)
    [audit] = await audits(db_session, "reservation.updated")
    assert audit.before is not None and audit.after is not None
    assert (audit.before["room_number"], audit.after["room_id"]) == ("101", None)


async def test_assigned_room_is_released_when_the_type_changes(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 4, room=hotel.rooms["101"])

    response = await change(client, r, room_type_id=hotel.dlx.id)

    assert response.status_code == 200, response.text
    assert response.json()["room_id"] is None


# --- modify: checked in ------------------------------------------------------------------


@pytest.mark.parametrize(("new_check_out", "nights"), [(3, 5), (0, 2)])
async def test_in_house_guest_can_extend_or_shorten(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    new_check_out: int,
    nights: int,
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, -2, 1, RS.CHECKED_IN, room=hotel.rooms["101"])

    response = await change(client, r, check_out_date=str(day(new_check_out)), notes="Stays on")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["nights"], body["room_number"], body["status"]) == (nights, "101", "checked_in")


async def test_in_house_stay_cannot_end_before_today(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, -2, 1, RS.CHECKED_IN, room=hotel.rooms["101"])

    response = await change(client, r, check_out_date=str(day(-1)))

    assert response.status_code == 422
    assert response.json()["code"] == "check_out_in_past"


@pytest.mark.parametrize(
    "body",
    [
        {"check_in_date": "2026-03-07"},
        {"room_type_id": "dlx"},
        {"adults": 2},
        {"is_vip": True},
    ],
)
async def test_in_house_guest_cannot_change_anything_else(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    body: dict[str, Any],
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, -2, 1, RS.CHECKED_IN, room=hotel.rooms["101"])
    if body.get("room_type_id") == "dlx":
        body = {"room_type_id": hotel.dlx.id}

    response = await change(client, r, **body)

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_transition"


async def test_in_house_extension_needs_the_room_free(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, -2, 1, RS.CHECKED_IN, room=hotel.rooms["101"])
    await stay(db_session, hotel.std, 2, 4, room=hotel.rooms["101"])  # next guest's room

    response = await change(client, r, check_out_date=str(day(3)))

    assert response.status_code == 409
    assert response.json()["code"] == "room_not_available"
    assert (await fresh(db_session, r)).check_out_date == day(1)


@pytest.mark.parametrize("status", [RS.CANCELLED, RS.NO_SHOW, RS.CHECKED_OUT])
async def test_finished_bookings_cannot_change(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    status: ReservationStatus,
) -> None:
    await login_as(Roles.FRONT_OFFICE_MANAGER)
    room = hotel.rooms["102"] if status is RS.CHECKED_OUT else None
    r = await stay(db_session, hotel.std, -3, -1, status, room=room)

    for response in [
        await change(client, r, notes="x"),
        await cancel(client, r),
        await no_show(client, r),
    ]:
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "invalid_transition"


# --- cancel ------------------------------------------------------------------------------


async def test_cancel_frees_the_nights_and_keeps_the_rows(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    published: list[DomainEvent],
) -> None:
    user = await login_as(Roles.FRONT_OFFICE_MANAGER)
    r = await stay(db_session, hotel.std, 0, 2, room=hotel.rooms["101"])
    await stay(db_session, hotel.std, 0, 2)  # Standard is now full tonight

    response = await cancel(client, r, reason="  Flight cancelled ")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["room_id"], body["version"]) == ("cancelled", None, 2)
    r = await fresh(db_session, r)
    assert (r.cancelled_by, r.cancellation_reason) == (user.id, "Flight cancelled")
    assert r.cancelled_at is not None
    assert len(await nights_of(db_session, r)) == 2  # never deleted
    [audit] = await audits(db_session, "reservation.cancelled")
    assert audit.before == {
        "status": "confirmed",
        "room_id": hotel.rooms["101"].id,
        "room_number": "101",
    }
    assert audit.after == {
        "status": "cancelled",
        "reason": "Flight cancelled",
        "room_id": None,
        "room_number": None,
        "confirmation_code": r.confirmation_code,
    }
    [history] = body["history"]
    assert (history["status_before"], history["status_after"], history["reason"]) == (
        "confirmed",
        "cancelled",
        "Flight cancelled",
    )
    assert isinstance(published[0], ReservationChanged) and published[0].change == "cancelled"
    assert [e.room.number for e in published if isinstance(e, RoomStatusChanged)] == ["101"]
    # The night is for sale again.
    availability = await client.get(
        "/api/reservations/availability",
        params={"check_in": str(day(0)), "check_out": str(day(1))},
    )
    std = next(t for t in availability.json()["room_types"] if t["code"] == "STD")
    assert std["available"] == 1


@pytest.mark.parametrize("reason", [None, "", "   ", "x" * 501])
async def test_cancel_needs_a_reason(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    reason: str | None,
) -> None:
    await login_as(Roles.FRONT_OFFICE_MANAGER)
    r = await stay(db_session, hotel.std, 2, 4)

    assert (await cancel(client, r, reason=reason)).status_code == 422


async def test_cannot_cancel_a_guest_in_house_or_twice(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.FRONT_OFFICE_MANAGER)
    in_house = await stay(db_session, hotel.std, -1, 1, RS.CHECKED_IN, room=hotel.rooms["101"])
    r = await stay(db_session, hotel.std, 2, 4)

    refused = await cancel(client, in_house)
    assert (refused.status_code, refused.json()["code"]) == (409, "invalid_transition")
    assert "already checked in" in refused.json()["detail"]
    assert (await cancel(client, r)).status_code == 200
    assert (await cancel(client, r, version=2)).status_code == 409


# --- no-show -----------------------------------------------------------------------------


@pytest.mark.parametrize("check_in", [0, -1])
async def test_no_show_from_the_arrival_day(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    published: list[DomainEvent],
    check_in: int,
) -> None:
    user = await login_as(Roles.FRONT_OFFICE_MANAGER)
    r = await stay(db_session, hotel.std, check_in, 2, room=hotel.rooms["101"])

    response = await no_show(client, r)

    assert response.status_code == 200, response.text
    assert (response.json()["status"], response.json()["room_id"]) == ("no_show", None)
    r = await fresh(db_session, r)
    assert (r.cancelled_by, r.cancellation_reason) == (user.id, None)
    [audit] = await audits(db_session, "reservation.marked_no_show")
    assert audit.after is not None and audit.after["status"] == "no_show"
    assert isinstance(published[0], ReservationChanged) and published[0].change == "no_show"


async def test_no_show_before_the_arrival_day_is_refused(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.FRONT_OFFICE_MANAGER)
    r = await stay(db_session, hotel.std, 1, 2)

    response = await no_show(client, r)

    assert response.status_code == 409
    assert response.json()["code"] == "too_early"
    assert (await fresh(db_session, r)).status is RS.CONFIRMED


async def test_guest_in_house_is_no_no_show(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.FRONT_OFFICE_MANAGER)
    r = await stay(db_session, hotel.std, -1, 1, RS.CHECKED_IN, room=hotel.rooms["101"])

    response = await no_show(client, r)

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_transition"


# --- lookups and history -----------------------------------------------------------------


async def test_lookup_by_code(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    r = await stay(db_session, hotel.std, 2, 4, confirmation_code="KX7M2P9Q")

    found = await client.get("/api/reservations/by-code/kx7m2p9q")

    assert found.status_code == 200
    assert (found.json()["id"], found.json()["history"]) == (r.id, [])
    assert (await client.get("/api/reservations/by-code/ZZZZ2222")).status_code == 404
    assert (await client.get("/api/reservations/by-code/short")).status_code == 422


async def test_other_properties_reservations_are_not_found(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession, fixed_today: None
) -> None:
    other = Property(name="Other Hotel", time_zone="UTC")
    db_session.add(other)
    await db_session.flush()
    theirs_type = RoomType(property_id=other.id, code="STD", name="Std", max_occupancy=2)
    db_session.add(theirs_type)
    await db_session.flush()
    theirs = await stay(db_session, theirs_type, 2, 4, confirmation_code="THERE234")
    await login_as(Roles.FRONT_OFFICE_MANAGER)

    for response in [
        await client.get(f"/api/reservations/{theirs.id}"),
        await client.get("/api/reservations/by-code/THERE234"),
        await change(client, theirs, notes="x"),
        await cancel(client, theirs),
    ]:
        assert response.status_code == 404


async def test_history_follows_the_booking(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel
) -> None:
    desk = await login_as(Roles.FRONT_OFFICE_MANAGER)
    booked = await client.post(
        "/api/reservations",
        headers={"Idempotency-Key": uuid.uuid4().hex},
        json={
            "guest": {"first_name": "Ada", "last_name": "Lovelace"},
            "room_type_id": hotel.std.id,
            "check_in_date": str(day(2)),
            "check_out_date": str(day(4)),
            "source": "email",
        },
    )
    rid, code = booked.json()["id"], booked.json()["confirmation_code"]
    await client.patch(f"/api/reservations/{rid}", json={"version": 1, "adults": 2})
    await client.post(f"/api/reservations/{rid}/cancel", json={"version": 2, "reason": "Ill"})

    body = (await client.get(f"/api/reservations/by-code/{code}")).json()

    assert [
        (h["action"], h["status_before"], h["status_after"], h["actor"]["id"])
        for h in body["history"]
    ] == [
        ("reservation.created", None, "confirmed", desk.id),
        ("reservation.updated", None, None, desk.id),
        ("reservation.cancelled", "confirmed", "cancelled", desk.id),
    ]
    assert "adults" in body["history"][1]["changed"]


# --- permissions -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "modify", "cancel_status"),
    [
        (Roles.RECEPTIONIST, 200, 403),  # may change bookings, not cancel them
        (Roles.FRONT_OFFICE_MANAGER, 200, 200),
        (Roles.GENERAL_MANAGER, 200, 200),
        (Roles.HOUSEKEEPING_SUPERVISOR, 403, 403),
    ],
)
async def test_change_permissions(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    role: str,
    modify: int,
    cancel_status: int,
) -> None:
    await login_as(role)
    r = await stay(db_session, hotel.std, 0, 2)
    other = await stay(db_session, hotel.std, 0, 2)

    assert (await change(client, r, notes="x")).status_code == modify
    assert (await cancel(client, other)).status_code == cancel_status
    if cancel_status == 403:
        assert (await no_show(client, other)).status_code == 403


async def test_changes_require_login(client: AsyncClient) -> None:
    for response in [
        await client.patch("/api/reservations/1", json={"version": 1}),
        await client.post("/api/reservations/1/cancel", json={"version": 1, "reason": "x"}),
        await client.post("/api/reservations/1/no-show", json={"version": 1}),
        await client.get("/api/reservations/by-code/ABCD2345"),
    ]:
        assert response.status_code == 401
