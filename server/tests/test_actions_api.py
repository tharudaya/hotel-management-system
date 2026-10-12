"""Check-in, check-out and housekeeping status changes over HTTP."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, UOWTransaction

from app.api.deps import get_hotel_today
from app.models import (
    AuditEvent,
    AuditEventType,
    Guest,
    HousekeepingStatus,
    Reservation,
    ReservationStatus,
    Room,
    RoomType,
    UserRole,
)
from app.realtime.events import DomainEvent, RoomStatusChanged, SummaryChanged
from tests.conftest import LoginAs

TODAY: date = date(2026, 3, 10)
HK = HousekeepingStatus
RS = ReservationStatus


@dataclass
class Hotel:
    std: RoomType
    dlx: RoomType
    rooms: dict[str, Room]
    reservations: dict[str, Reservation]


@pytest.fixture
def fixed_today(app: FastAPI) -> None:
    app.dependency_overrides[get_hotel_today] = lambda: TODAY


@pytest.fixture
def published(app: FastAPI) -> list[DomainEvent]:
    """Events published on the app's bus during the test."""
    events: list[DomainEvent] = []

    async def collect(event: DomainEvent) -> None:
        events.append(event)

    app.state.event_bus.subscribe(collect)
    return events


def room_events(published: list[DomainEvent]) -> list[RoomStatusChanged]:
    return [evt for evt in published if isinstance(evt, RoomStatusChanged)]


def summary_events(published: list[DomainEvent]) -> list[SummaryChanged]:
    return [evt for evt in published if isinstance(evt, SummaryChanged)]


@pytest.fixture
async def hotel(db_session: AsyncSession, fixed_today: None) -> Hotel:
    std = RoomType(code="STD", name="Standard", max_occupancy=2)
    dlx = RoomType(code="DLX", name="Deluxe", max_occupancy=3)
    rooms = {
        "101": Room(number="101", floor=1, room_type=std, housekeeping_status=HK.CLEAN),
        "102": Room(number="102", floor=1, room_type=std, housekeeping_status=HK.INSPECTED),
        "103": Room(number="103", floor=1, room_type=std, housekeeping_status=HK.DIRTY),
        "104": Room(number="104", floor=1, room_type=std, housekeeping_status=HK.CLEAN),
        "105": Room(number="105", floor=1, room_type=dlx, housekeeping_status=HK.CLEAN),
        "106": Room(number="106", floor=1, room_type=std, housekeeping_status=HK.OUT_OF_ORDER),
    }

    def reservation(
        code: str, room: str | None, check_in: int, check_out: int, status: ReservationStatus
    ) -> Reservation:
        return Reservation(
            confirmation_code=code,
            guest=Guest(first_name=code.title(), last_name="Guest"),
            room_type=std,
            room=rooms[room] if room else None,
            check_in_date=TODAY + timedelta(days=check_in),
            check_out_date=TODAY + timedelta(days=check_out),
            status=status,
        )

    reservations = {
        # Arriving today, pre-assigned to 101.
        "arrival": reservation("arrival", "101", 0, 2, RS.CONFIRMED),
        # Arriving today, no room yet.
        "unassigned": reservation("unassigned", None, 0, 3, RS.CONFIRMED),
        # Arrives in two days.
        "future": reservation("future", None, 2, 4, RS.CONFIRMED),
        # In house in 104 (clean, so check-out visibly changes it to dirty).
        "inhouse": reservation("inhouse", "104", -2, 1, RS.CHECKED_IN),
        "cancelled": reservation("cancelled", None, 0, 2, RS.CANCELLED),
    }
    db_session.add_all([*rooms.values(), *reservations.values()])
    await db_session.flush()
    return Hotel(std=std, dlx=dlx, rooms=rooms, reservations=reservations)


async def audit_rows(db: AsyncSession, event_type: AuditEventType) -> list[AuditEvent]:
    query = select(AuditEvent).where(AuditEvent.event_type == event_type.value)
    return list((await db.scalars(query.order_by(AuditEvent.id))).all())


async def refresh(db: AsyncSession, *objects: object) -> None:
    for obj in objects:
        await db.refresh(obj)


def check_in_url(reservation: Reservation) -> str:
    return f"/api/reservations/{reservation.id}/check-in"


def check_out_url(reservation: Reservation) -> str:
    return f"/api/reservations/{reservation.id}/check-out"


def housekeeping_url(room: Room) -> str:
    return f"/api/rooms/{room.id}/housekeeping-status"


@pytest.fixture
def concurrent_writer(db_session: AsyncSession) -> Callable[[Room], None]:
    """Simulate another transaction updating the room between our read and our write.

    Bumps the room's version in the database right before the service's flush,
    exactly the window optimistic locking has to protect.
    """

    def arm(room: Room) -> None:
        room_id = room.id
        fired = False

        def bump(session: Session, flush_context: UOWTransaction, instances: object) -> None:
            nonlocal fired
            if not fired:
                fired = True
                session.connection().execute(
                    text("UPDATE rooms SET version = version + 1 WHERE id = :id"), {"id": room_id}
                )

        event.listen(db_session.sync_session, "before_flush", bump)

    return arm


# --- check-in ------------------------------------------------------------------------


async def test_check_in_to_pre_assigned_room(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    published: list[DomainEvent],
    db_session: AsyncSession,
) -> None:
    user = await login_as(UserRole.FRONT_DESK)
    arrival, room = hotel.reservations["arrival"], hotel.rooms["101"]

    response = await client.post(
        check_in_url(arrival), json={"room_id": room.id, "room_version": 1}
    )

    assert response.status_code == 200, response.text
    entry = response.json()
    assert entry["room_id"] == room.id
    assert entry["occupancy_status"] == "occupied"
    assert entry["guest_name"] == "Arrival Guest"
    assert entry["reservation_id"] == arrival.id
    assert entry["nights_remaining"] == 2
    assert entry["version"] == 2

    await refresh(db_session, arrival, room)
    assert (arrival.status, arrival.room_id, room.version) == (RS.CHECKED_IN, room.id, 2)

    [audit] = await audit_rows(db_session, AuditEventType.RESERVATION_CHECKED_IN)
    assert (audit.user_id, audit.username) == (user.id, "frontdesk")
    assert audit.details == {
        "reservation_id": arrival.id,
        "confirmation_code": "arrival",
        "room_id": room.id,
        "room_number": "101",
        "before": {"status": "confirmed", "room_id": room.id},
        "after": {"status": "checked_in", "room_id": room.id},
    }

    room_evt, summary_evt = published
    assert isinstance(room_evt, RoomStatusChanged)
    assert room_evt.business_date == TODAY
    assert room_evt.room.model_dump(mode="json") == entry
    # The summary follows the room events and reflects the check-in.
    assert isinstance(summary_evt, SummaryChanged)
    assert summary_evt.summary.business_date == TODAY
    assert summary_evt.summary.arrivals_checked_in == 1
    assert summary_evt.summary.in_house == 2


async def test_check_in_to_different_room_updates_both_rooms(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    published: list[DomainEvent],
) -> None:
    await login_as(UserRole.FRONT_DESK)
    arrival = hotel.reservations["arrival"]
    old_room, new_room = hotel.rooms["101"], hotel.rooms["102"]

    response = await client.post(
        check_in_url(arrival), json={"room_id": new_room.id, "room_version": 1}
    )

    assert response.status_code == 200, response.text
    by_room = {evt.room.room_id: evt.room for evt in room_events(published)}
    assert len(summary_events(published)) == 1
    assert set(by_room) == {old_room.id, new_room.id}
    assert by_room[new_room.id].occupancy_status == "occupied"
    assert by_room[old_room.id].occupancy_status == "vacant"


async def test_check_in_unassigned_reservation(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel
) -> None:
    await login_as(UserRole.MANAGER)
    room = hotel.rooms["102"]

    response = await client.post(
        check_in_url(hotel.reservations["unassigned"]),
        json={"room_id": room.id, "room_version": 1},
    )

    assert response.status_code == 200, response.text
    assert response.json()["guest_name"] == "Unassigned Guest"


@pytest.mark.parametrize(
    ("reservation", "room", "code", "message"),
    [
        ("arrival", "103", "room_not_ready", "Room 103 is dirty; it must be clean or inspected."),
        ("arrival", "106", "room_not_ready", "Room 106 is out_of_order; it must be clean or"),
        ("arrival", "105", "room_type_mismatch", "Room 105 is not of the reserved room type."),
        ("unassigned", "104", "room_not_vacant", "Room 104 is occupied."),
        ("unassigned", "101", "room_not_vacant", "Room 101 is assigned to another reservation"),
        ("future", "102", "too_early", "Reservation arrives on 2026-03-12;"),
        (
            "inhouse",
            "102",
            "invalid_transition",
            "Cannot check in: the reservation is already checked in.",
        ),
        (
            "cancelled",
            "102",
            "invalid_transition",
            "Cannot check in: the reservation is cancelled.",
        ),
    ],
)
async def test_check_in_rejected(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    published: list[DomainEvent],
    db_session: AsyncSession,
    reservation: str,
    room: str,
    code: str,
    message: str,
) -> None:
    await login_as(UserRole.FRONT_DESK)
    target = hotel.reservations[reservation]
    status_before = target.status

    response = await client.post(
        check_in_url(target), json={"room_id": hotel.rooms[room].id, "room_version": 1}
    )

    assert response.status_code == 409
    assert response.json()["code"] == code
    assert response.json()["detail"].startswith(message)
    await refresh(db_session, target)
    assert target.status == status_before
    assert await audit_rows(db_session, AuditEventType.RESERVATION_CHECKED_IN) == []
    assert published == []


async def test_check_in_not_found(client: AsyncClient, login_as: LoginAs, hotel: Hotel) -> None:
    await login_as(UserRole.FRONT_DESK)

    missing_reservation = await client.post(
        "/api/reservations/999999/check-in",
        json={"room_id": hotel.rooms["101"].id, "room_version": 1},
    )
    missing_room = await client.post(
        check_in_url(hotel.reservations["arrival"]), json={"room_id": 999999, "room_version": 1}
    )

    assert missing_reservation.status_code == 404
    assert missing_reservation.json() == {
        "detail": "Reservation 999999 not found",
        "code": "not_found",
    }
    assert missing_room.status_code == 404
    assert missing_room.json()["code"] == "not_found"


@pytest.mark.parametrize(
    "body", [{}, {"room_id": 1}, {"room_version": 1}, {"room_id": 0, "room_version": 1}]
)
async def test_check_in_validation(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, body: dict[str, int]
) -> None:
    await login_as(UserRole.FRONT_DESK)

    response = await client.post(check_in_url(hotel.reservations["arrival"]), json=body)

    assert response.status_code == 422


async def test_check_in_stale_version_conflict(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    published: list[DomainEvent],
    db_session: AsyncSession,
) -> None:
    await login_as(UserRole.FRONT_DESK)
    arrival, room = hotel.reservations["arrival"], hotel.rooms["101"]
    # Someone else already changed the room (version 1 -> 2).
    await db_session.execute(text("UPDATE rooms SET version = 2 WHERE id = :id"), {"id": room.id})

    response = await client.post(
        check_in_url(arrival), json={"room_id": room.id, "room_version": 1}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "version_conflict"
    await refresh(db_session, arrival)
    assert arrival.status == RS.CONFIRMED
    assert published == []


async def test_check_in_concurrent_update_conflict(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    published: list[DomainEvent],
    db_session: AsyncSession,
    concurrent_writer: Callable[[Room], None],
) -> None:
    await login_as(UserRole.FRONT_DESK)
    arrival, room = hotel.reservations["arrival"], hotel.rooms["101"]
    concurrent_writer(room)

    response = await client.post(
        check_in_url(arrival), json={"room_id": room.id, "room_version": 1}
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Room 101 was changed by someone else. Reload and try again.",
        "code": "version_conflict",
    }
    # The whole transaction was rolled back: reservation, room and audit row.
    await refresh(db_session, arrival, room)
    assert (arrival.status, room.version) == (RS.CONFIRMED, 1)
    assert await audit_rows(db_session, AuditEventType.RESERVATION_CHECKED_IN) == []
    assert published == []


# --- check-out -----------------------------------------------------------------------


async def test_check_out_makes_room_dirty(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    published: list[DomainEvent],
    db_session: AsyncSession,
) -> None:
    user = await login_as(UserRole.FRONT_DESK)
    guest, room = hotel.reservations["inhouse"], hotel.rooms["104"]

    response = await client.post(check_out_url(guest), json={"room_version": 1})

    assert response.status_code == 200, response.text
    entry = response.json()
    assert entry["room_id"] == room.id
    assert entry["occupancy_status"] == "vacant"
    assert entry["housekeeping_status"] == "dirty"
    assert entry["guest_name"] is None
    assert entry["version"] == 2

    await refresh(db_session, guest)
    assert guest.status == RS.CHECKED_OUT
    [audit] = await audit_rows(db_session, AuditEventType.RESERVATION_CHECKED_OUT)
    assert audit.user_id == user.id
    assert audit.details is not None
    assert audit.details["before"] == {"status": "checked_in", "housekeeping_status": "clean"}
    assert audit.details["after"] == {"status": "checked_out", "housekeeping_status": "dirty"}
    [evt] = room_events(published)
    assert evt.room.model_dump(mode="json") == entry
    [summary_evt] = summary_events(published)
    assert summary_evt.summary.in_house == 0  # early check-out: guest was due out tomorrow


async def test_check_out_requires_checked_in(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, published: list[DomainEvent]
) -> None:
    await login_as(UserRole.FRONT_DESK)

    response = await client.post(
        check_out_url(hotel.reservations["arrival"]), json={"room_version": 1}
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Cannot check out: the reservation is not checked in yet.",
        "code": "invalid_transition",
    }
    assert published == []


async def test_check_out_twice(client: AsyncClient, login_as: LoginAs, hotel: Hotel) -> None:
    await login_as(UserRole.FRONT_DESK)
    url = check_out_url(hotel.reservations["inhouse"])

    assert (await client.post(url, json={"room_version": 1})).status_code == 200
    second = await client.post(url, json={"room_version": 2})

    assert second.status_code == 409
    assert second.json()["code"] == "invalid_transition"


async def test_check_out_stale_version_conflict(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(UserRole.FRONT_DESK)
    guest = hotel.reservations["inhouse"]

    response = await client.post(check_out_url(guest), json={"room_version": 7})

    assert response.status_code == 409
    assert response.json()["code"] == "version_conflict"
    await refresh(db_session, guest)
    assert guest.status == RS.CHECKED_IN


async def test_check_out_concurrent_update_conflict(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    concurrent_writer: Callable[[Room], None],
) -> None:
    await login_as(UserRole.FRONT_DESK)
    guest, room = hotel.reservations["inhouse"], hotel.rooms["104"]
    concurrent_writer(room)

    response = await client.post(check_out_url(guest), json={"room_version": 1})

    assert response.status_code == 409
    assert response.json()["code"] == "version_conflict"
    await refresh(db_session, guest, room)
    assert (guest.status, room.housekeeping_status) == (RS.CHECKED_IN, HK.CLEAN)


# --- housekeeping status -------------------------------------------------------------


async def test_housekeeping_status_change(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    published: list[DomainEvent],
    db_session: AsyncSession,
) -> None:
    user = await login_as(UserRole.HOUSEKEEPING)
    room = hotel.rooms["103"]  # dirty

    started = await client.patch(
        housekeeping_url(room), json={"status": "in_progress", "version": 1}
    )
    finished = await client.patch(housekeeping_url(room), json={"status": "clean", "version": 2})

    assert started.status_code == 200, started.text
    assert finished.status_code == 200, finished.text
    assert finished.json()["housekeeping_status"] == "clean"
    assert finished.json()["version"] == 3

    first, second = await audit_rows(db_session, AuditEventType.ROOM_HOUSEKEEPING_STATUS_CHANGED)
    assert first.user_id == user.id
    assert first.details == {
        "room_id": room.id,
        "room_number": "103",
        "before": {"housekeeping_status": "dirty", "version": 1},
        "after": {"housekeeping_status": "in_progress", "version": 2},
    }
    assert second.details is not None
    assert second.details["after"] == {"housekeeping_status": "clean", "version": 3}
    assert [evt.room.housekeeping_status for evt in room_events(published)] == [
        "in_progress",
        "clean",
    ]
    assert len(summary_events(published)) == 2


async def test_housekeeping_invalid_transition(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    published: list[DomainEvent],
    db_session: AsyncSession,
) -> None:
    await login_as(UserRole.HOUSEKEEPING)
    room = hotel.rooms["106"]  # out_of_order

    response = await client.patch(housekeeping_url(room), json={"status": "clean", "version": 1})

    assert response.status_code == 409
    assert response.json() == {
        "detail": (
            "Cannot change housekeeping status from out_of_order to clean. "
            "Allowed from out_of_order: dirty."
        ),
        "code": "invalid_transition",
    }
    await refresh(db_session, room)
    assert (room.housekeeping_status, room.version) == (HK.OUT_OF_ORDER, 1)
    assert await audit_rows(db_session, AuditEventType.ROOM_HOUSEKEEPING_STATUS_CHANGED) == []
    assert published == []


async def test_housekeeping_same_status_rejected(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel
) -> None:
    await login_as(UserRole.HOUSEKEEPING)

    response = await client.patch(
        housekeeping_url(hotel.rooms["101"]), json={"status": "clean", "version": 1}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_transition"


@pytest.mark.parametrize(
    "body",
    [
        {"status": "sparkling", "version": 1},
        {"status": "clean"},
        {"version": 1},
        {"status": "clean", "version": 0},
    ],
)
async def test_housekeeping_validation(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, body: dict[str, object]
) -> None:
    await login_as(UserRole.HOUSEKEEPING)

    response = await client.patch(housekeeping_url(hotel.rooms["103"]), json=body)

    assert response.status_code == 422


async def test_housekeeping_room_not_found(client: AsyncClient, login_as: LoginAs) -> None:
    await login_as(UserRole.HOUSEKEEPING)

    response = await client.patch(
        "/api/rooms/999999/housekeeping-status", json={"status": "clean", "version": 1}
    )

    assert response.status_code == 404


async def test_housekeeping_second_writer_with_stale_version_gets_409(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, published: list[DomainEvent]
) -> None:
    """Two people looking at version 1 of the same room: the first change wins."""
    await login_as(UserRole.HOUSEKEEPING)
    url = housekeeping_url(hotel.rooms["103"])

    first = await client.patch(url, json={"status": "in_progress", "version": 1})
    second = await client.patch(url, json={"status": "out_of_order", "version": 1})

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json() == {
        "detail": (
            "Room 103 was changed by someone else (version 2, you sent 1). Reload and try again."
        ),
        "code": "version_conflict",
    }
    assert len(room_events(published)) == 1


async def test_housekeeping_concurrent_update_conflict(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    published: list[DomainEvent],
    db_session: AsyncSession,
    concurrent_writer: Callable[[Room], None],
) -> None:
    await login_as(UserRole.HOUSEKEEPING)
    room = hotel.rooms["103"]
    concurrent_writer(room)

    response = await client.patch(
        housekeeping_url(room), json={"status": "in_progress", "version": 1}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "version_conflict"
    await refresh(db_session, room)
    assert (room.housekeeping_status, room.version) == (HK.DIRTY, 1)
    assert await audit_rows(db_session, AuditEventType.ROOM_HOUSEKEEPING_STATUS_CHANGED) == []
    assert published == []


# --- permissions ---------------------------------------------------------------------


@pytest.mark.parametrize("action", ["check-in", "check-out"])
async def test_housekeeping_role_cannot_check_in_or_out(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, action: str
) -> None:
    await login_as(UserRole.HOUSEKEEPING)
    reservation = hotel.reservations["arrival" if action == "check-in" else "inhouse"]

    response = await client.post(
        f"/api/reservations/{reservation.id}/{action}",
        json={"room_id": hotel.rooms["102"].id, "room_version": 1},
    )

    assert response.status_code == 403


async def test_front_desk_cannot_change_housekeeping_status(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel
) -> None:
    await login_as(UserRole.FRONT_DESK)

    response = await client.patch(
        housekeeping_url(hotel.rooms["103"]), json={"status": "in_progress", "version": 1}
    )

    assert response.status_code == 403


@pytest.mark.parametrize("role", [UserRole.MANAGER, UserRole.ADMIN])
async def test_managers_can_do_everything(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, role: UserRole
) -> None:
    await login_as(role)

    checked_in = await client.post(
        check_in_url(hotel.reservations["arrival"]),
        json={"room_id": hotel.rooms["101"].id, "room_version": 1},
    )
    checked_out = await client.post(
        check_out_url(hotel.reservations["inhouse"]), json={"room_version": 1}
    )
    cleaned = await client.patch(
        housekeeping_url(hotel.rooms["103"]), json={"status": "clean", "version": 1}
    )

    assert [checked_in.status_code, checked_out.status_code, cleaned.status_code] == [200] * 3


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/reservations/1/check-in"),
        ("POST", "/api/reservations/1/check-out"),
        ("PATCH", "/api/rooms/1/housekeeping-status"),
    ],
)
async def test_actions_require_login(client: AsyncClient, method: str, path: str) -> None:
    response = await client.request(method, path, json={"room_id": 1, "room_version": 1})

    assert response.status_code == 401
