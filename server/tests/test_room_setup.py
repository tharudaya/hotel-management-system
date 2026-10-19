"""Room setup API: room types and rooms, every rule, permissions, audit and events."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hotel_today
from app.models import (
    AuditLog,
    HousekeepingStatus,
    Property,
    Reservation,
    ReservationStatus,
    Room,
    RoomType,
)
from app.realtime.events import DomainEvent, RoomSetupChanged
from app.services.permissions import get_default_property_id
from tests.conftest import LoginAs, Roles
from tests.factories import new_reservation

TODAY = date(2026, 3, 10)
TODAY_DT = datetime(2026, 3, 9, 12, tzinfo=UTC)  # a retirement moment before "today"
TYPES = "/api/admin/room-types"
ROOMS = "/api/admin/rooms"


# --- fixtures and helpers ------------------------------------------------------------


@pytest.fixture(autouse=True)
def fixed_today(app: FastAPI) -> None:
    app.dependency_overrides[get_hotel_today] = lambda: TODAY


@pytest.fixture
def published(app: FastAPI) -> list[DomainEvent]:
    events: list[DomainEvent] = []

    async def collect(event: DomainEvent) -> None:
        events.append(event)

    app.state.event_bus.subscribe(collect)
    return events


@pytest.fixture
async def admin(login_as: LoginAs) -> None:
    await login_as(Roles.ADMIN)


async def make_type(
    db: AsyncSession, code: str = "STD", name: str = "Standard", *, active: bool = True
) -> RoomType:
    room_type = RoomType(
        property_id=await get_default_property_id(db),
        code=code,
        name=name,
        max_occupancy=2,
        is_active=active,
    )
    db.add(room_type)
    await db.flush()
    return room_type


async def make_room(db: AsyncSession, number: str, room_type: RoomType, floor: int = 1) -> Room:
    room = Room(property_id=room_type.property_id, number=number, floor=floor, room_type=room_type)
    db.add(room)
    await db.flush()
    return room


async def book(
    db: AsyncSession,
    room_type: RoomType,
    *,
    room: Room | None = None,
    status: ReservationStatus = ReservationStatus.CONFIRMED,
    nights: tuple[int, int] = (2, 4),
    code: str | None = None,
) -> Reservation:
    reservation = new_reservation(
        room_type.id,
        property_id=room_type.property_id,
        confirmation_code=code,
        room=room.id if room else None,
        check_in_date=TODAY + timedelta(days=nights[0]),
        check_out_date=TODAY + timedelta(days=nights[1]),
        status=status,
    )
    db.add(reservation)
    await db.flush()
    return reservation


async def audit_rows(db: AsyncSession, action: str) -> list[AuditLog]:
    rows = await db.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
    return list(rows.all())


def setup_events(published: list[DomainEvent]) -> list[RoomSetupChanged]:
    return [e for e in published if isinstance(e, RoomSetupChanged)]


# --- room types ------------------------------------------------------------------------


@pytest.mark.usefixtures("admin")
async def test_list_room_types_with_counts(client: AsyncClient, db_session: AsyncSession) -> None:
    std = await make_type(db_session, "STD", "Standard")
    dlx = await make_type(db_session, "DLX", "Deluxe")
    await make_type(db_session, "OLD", "Old wing", active=False)
    await make_room(db_session, "101", std)
    await make_room(db_session, "102", std)
    await book(db_session, dlx)

    active = (await client.get(TYPES)).json()
    everything = (await client.get(TYPES, params={"include_retired": True})).json()

    assert [t["code"] for t in active] == ["DLX", "STD"]  # by name
    named = {t["code"]: t for t in everything}
    assert set(named) == {"STD", "DLX", "OLD"}
    assert (named["STD"]["active_room_count"], named["STD"]["upcoming_reservations"]) == (2, 0)
    assert named["STD"]["retire_blocked"] == (
        "Room type STD still has 2 active rooms. Retire them or give them another type first."
    )
    assert named["DLX"]["retire_blocked"] == "Room type DLX has 1 upcoming reservation."
    assert named["OLD"]["is_active"] is False and named["OLD"]["retire_blocked"] is None


@pytest.mark.usefixtures("admin")
async def test_create_room_type(
    client: AsyncClient, db_session: AsyncSession, published: list[DomainEvent]
) -> None:
    response = await client.post(
        TYPES,
        json={
            "code": " dlx ",
            "name": "  Deluxe King ",
            "max_occupancy": 3,
            "bed_setup": "1 king",
            "description": "Sea view",
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert (data["code"], data["name"], data["is_active"], data["active_room_count"]) == (
        "DLX",
        "Deluxe King",
        True,
        0,
    )
    [row] = await audit_rows(db_session, "room_type.created")
    assert (row.target_type, row.target_id) == ("room_type", str(data["id"]))
    assert row.after == {
        "code": "DLX",
        "name": "Deluxe King",
        "max_occupancy": 3,
        "bed_setup": "1 king",
        "description": "Sea view",
    }
    assert len(setup_events(published)) == 1


@pytest.mark.usefixtures("admin")
@pytest.mark.parametrize(
    ("body", "code", "message"),
    [
        (
            {"code": "std", "name": "Another"},
            "room_type_code_taken",
            "A room type with the code STD already exists.",
        ),
        (
            {"code": "OTH", "name": "STANDARD"},
            "room_type_name_taken",
            "A room type named 'Standard' already exists.",
        ),
        (
            {"code": "old", "name": "New old"},
            "room_type_code_taken",
            "A room type with the code OLD already exists (retired; reactivate it instead).",
        ),
    ],
)
async def test_room_type_codes_and_names_are_unique_ignoring_case(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any], code: str, message: str
) -> None:
    await make_type(db_session, "STD", "Standard")
    await make_type(db_session, "OLD", "Old wing", active=False)

    response = await client.post(TYPES, json={**body, "max_occupancy": 2})

    assert response.status_code == 409
    assert response.json() == {"detail": message, "code": code}


@pytest.mark.usefixtures("admin")
@pytest.mark.parametrize(
    "body",
    [
        {"code": "STD", "name": "Std", "max_occupancy": 0},
        {"code": "STD", "name": "Std", "max_occupancy": 13},
        {"code": "S T", "name": "Std", "max_occupancy": 2},
        {"code": "", "name": "Std", "max_occupancy": 2},
        {"code": "STD", "name": "   ", "max_occupancy": 2},
        {"code": "STD", "name": "Std", "max_occupancy": 2, "property_id": 9},
        {"code": "STD", "name": "Std"},
    ],
)
async def test_room_type_validation(client: AsyncClient, body: dict[str, Any]) -> None:
    assert (await client.post(TYPES, json=body)).status_code == 422


@pytest.mark.usefixtures("admin")
async def test_update_room_type(
    client: AsyncClient, db_session: AsyncSession, published: list[DomainEvent]
) -> None:
    std = await make_type(db_session, "STD", "Standard")
    std.bed_setup = "2 singles"
    await db_session.flush()

    response = await client.patch(
        f"{TYPES}/{std.id}", json={"name": "Standard Twin", "max_occupancy": 3, "bed_setup": None}
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert (data["name"], data["max_occupancy"], data["bed_setup"]) == ("Standard Twin", 3, None)
    [row] = await audit_rows(db_session, "room_type.updated")
    assert row.before == {"name": "Standard", "max_occupancy": 2, "bed_setup": "2 singles"}
    assert row.after == {"name": "Standard Twin", "max_occupancy": 3, "bed_setup": None}
    assert len(setup_events(published)) == 1

    # Sending the same values again changes nothing and records nothing.
    again = await client.patch(f"{TYPES}/{std.id}", json={"name": "Standard Twin"})
    assert again.status_code == 200
    assert len(await audit_rows(db_session, "room_type.updated")) == 1


@pytest.mark.usefixtures("admin")
@pytest.mark.parametrize(
    "body", [{"name": None}, {"code": None}, {"max_occupancy": None}, {"x": 1}]
)
async def test_update_room_type_validation(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    std = await make_type(db_session)

    assert (await client.patch(f"{TYPES}/{std.id}", json=body)).status_code == 422


@pytest.mark.usefixtures("admin")
async def test_cannot_rename_a_room_type_onto_another(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_type(db_session, "STD", "Standard")
    dlx = await make_type(db_session, "DLX", "Deluxe")

    response = await client.patch(f"{TYPES}/{dlx.id}", json={"code": "Std"})

    assert response.status_code == 409
    assert response.json()["code"] == "room_type_code_taken"


@pytest.mark.usefixtures("admin")
async def test_room_type_with_active_rooms_cannot_be_retired(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    std = await make_type(db_session)
    await make_room(db_session, "101", std)

    response = await client.post(f"{TYPES}/{std.id}/retire")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Room type STD still has 1 active room. Retire them or give them another "
        "type first.",
        "code": "room_type_in_use",
    }


@pytest.mark.usefixtures("admin")
@pytest.mark.parametrize(
    ("status", "nights", "blocks"),
    [
        (ReservationStatus.CONFIRMED, (2, 4), True),  # future
        (ReservationStatus.CONFIRMED, (-1, 1), True),  # current stay, not yet over
        (ReservationStatus.CHECKED_IN, (-5, -1), True),  # overstaying guest
        (ReservationStatus.CONFIRMED, (-3, 0), False),  # ends today
        (ReservationStatus.CHECKED_OUT, (-3, -1), False),
        (ReservationStatus.CANCELLED, (2, 4), False),
    ],
)
async def test_room_type_with_upcoming_reservations_cannot_be_retired(
    client: AsyncClient,
    db_session: AsyncSession,
    status: ReservationStatus,
    nights: tuple[int, int],
    blocks: bool,
) -> None:
    std = await make_type(db_session)
    other = await make_type(db_session, "OTH", "Other")
    # A checked-in or checked-out stay needs a room (of another type, so only the
    # reservation's booked type matters here).
    room = (
        await make_room(db_session, "901", other)
        if status in (ReservationStatus.CHECKED_IN, ReservationStatus.CHECKED_OUT)
        else None
    )
    await book(db_session, std, room=room, status=status, nights=nights)

    response = await client.post(f"{TYPES}/{std.id}/retire")

    assert response.status_code == (409 if blocks else 200), response.text


@pytest.mark.usefixtures("admin")
async def test_retire_and_reactivate_room_type(
    client: AsyncClient, db_session: AsyncSession, published: list[DomainEvent]
) -> None:
    std = await make_type(db_session)

    retired = await client.post(f"{TYPES}/{std.id}/retire")
    twice = await client.post(f"{TYPES}/{std.id}/retire")
    listed = (await client.get(TYPES)).json()
    back = await client.post(f"{TYPES}/{std.id}/reactivate")
    again = await client.post(f"{TYPES}/{std.id}/reactivate")

    assert retired.status_code == 200 and retired.json()["is_active"] is False
    assert twice.status_code == 409 and twice.json()["code"] == "invalid_transition"
    assert listed == []  # hidden unless include_retired
    assert back.status_code == 200 and back.json()["is_active"] is True
    assert again.status_code == 409
    [r] = await audit_rows(db_session, "room_type.retired")
    [a] = await audit_rows(db_session, "room_type.reactivated")
    assert (r.before, r.after) == ({"is_active": True}, {"is_active": False, "code": "STD"})
    assert (a.before, a.after) == ({"is_active": False}, {"is_active": True, "code": "STD"})
    assert len(setup_events(published)) == 2


@pytest.mark.usefixtures("admin")
async def test_room_types_of_another_property_are_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    other = Property(name="Other Hotel", time_zone="UTC")
    db_session.add(other)
    await db_session.flush()
    theirs = RoomType(property_id=other.id, code="XX", name="Theirs", max_occupancy=2)
    db_session.add(theirs)
    await db_session.flush()

    for response in (
        await client.patch(f"{TYPES}/{theirs.id}", json={"name": "Mine"}),
        await client.post(f"{TYPES}/{theirs.id}/retire"),
        await client.post(f"{TYPES}/{theirs.id}/reactivate"),
        await client.post(ROOMS, json={"number": "1", "floor": 1, "room_type_id": theirs.id}),
    ):
        assert response.status_code == 404
    assert (await client.get(TYPES)).json() == []


# --- rooms: reading ---------------------------------------------------------------------


@pytest.mark.usefixtures("admin")
async def test_list_rooms_sorted_and_filtered(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    std = await make_type(db_session, "STD", "Standard")
    dlx = await make_type(db_session, "DLX", "Deluxe")
    for number, room_type, floor in [
        ("101", std, 1),
        ("9", std, 0),
        ("PH1", dlx, 9),
        ("1001", dlx, 10),
        ("10", std, 0),
    ]:
        await make_room(db_session, number, room_type, floor)
    gone = await make_room(db_session, "102", std)
    gone.is_active = False
    gone.retired_at = TODAY_DT
    await db_session.flush()

    def numbers(rooms: list[dict[str, Any]]) -> list[str]:
        return [r["number"] for r in rooms]

    assert numbers((await client.get(ROOMS)).json()) == ["9", "10", "101", "1001", "PH1"]
    assert numbers((await client.get(ROOMS, params={"floor": 0})).json()) == ["9", "10"]
    assert numbers((await client.get(ROOMS, params={"room_type_id": dlx.id})).json()) == [
        "1001",
        "PH1",
    ]
    assert numbers((await client.get(ROOMS, params={"status": "retired"})).json()) == ["102"]
    assert len((await client.get(ROOMS, params={"status": "all"})).json()) == 6
    assert (await client.get(ROOMS, params={"status": "gone"})).status_code == 422


@pytest.mark.usefixtures("admin")
async def test_room_entries_explain_what_is_blocked(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    std = await make_type(db_session)
    occupied = await make_room(db_session, "101", std)
    booked = await make_room(db_session, "102", std)
    await make_room(db_session, "103", std)
    await book(db_session, std, room=occupied, status=ReservationStatus.CHECKED_IN, nights=(-1, 2))
    await book(db_session, std, room=booked)
    await book(db_session, std, room=booked, nights=(5, 6))

    rooms = {r["number"]: r for r in (await client.get(ROOMS)).json()}

    assert rooms["101"]["guest_in_house"] is True
    assert rooms["101"]["retire_blocked"] == "Room 101 has a guest checked in."
    assert rooms["101"]["type_change_blocked"] == "Room 101 has a guest checked in."
    assert rooms["102"]["upcoming_reservations"] == 2
    assert rooms["102"]["retire_blocked"] == (
        "Room 102 has 2 upcoming reservations assigned. Move them to another room first."
    )
    assert rooms["102"]["type_change_blocked"] is None
    assert (rooms["103"]["retire_blocked"], rooms["103"]["type_change_blocked"]) == (None, None)
    assert rooms["103"]["room_type"] == {
        "id": std.id,
        "code": "STD",
        "name": "Standard",
        "is_active": True,
    }


# --- rooms: creating --------------------------------------------------------------------


@pytest.mark.usefixtures("admin")
async def test_create_room(
    client: AsyncClient, db_session: AsyncSession, published: list[DomainEvent]
) -> None:
    std = await make_type(db_session)

    response = await client.post(
        ROOMS, json={"number": " ph1 ", "floor": 9, "room_type_id": std.id, "notes": "Corner"}
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert (data["number"], data["floor"], data["notes"], data["version"]) == (
        "PH1",
        9,
        "Corner",
        1,
    )
    assert data["housekeeping_status"] == HousekeepingStatus.INSPECTED.value
    assert data["is_active"] is True
    [row] = await audit_rows(db_session, "room.created")
    assert row.after == {
        "number": "PH1",
        "floor": 9,
        "room_type_id": std.id,
        "notes": "Corner",
        "room_type_code": "STD",
        "housekeeping_status": "inspected",
    }
    assert len(setup_events(published)) == 1
    # It shows up on the front desk dashboard straight away.
    dashboard = (await client.get("/api/dashboard/rooms")).json()["rooms"]
    assert [r["number"] for r in dashboard] == ["PH1"]


@pytest.mark.usefixtures("admin")
@pytest.mark.parametrize(
    ("number", "message"),
    [
        ("101", "Room 101 already exists."),
        ("ph1", "Room PH1 already exists (retired; reactivate it instead)."),
    ],
)
async def test_room_numbers_are_unique_ignoring_case(
    client: AsyncClient, db_session: AsyncSession, number: str, message: str
) -> None:
    std = await make_type(db_session)
    await make_room(db_session, "101", std)
    old = await make_room(db_session, "PH1", std)
    old.is_active, old.retired_at = False, TODAY_DT
    await db_session.flush()

    response = await client.post(ROOMS, json={"number": number, "floor": 1, "room_type_id": std.id})

    assert response.status_code == 409
    assert response.json() == {"detail": message, "code": "room_number_taken"}


@pytest.mark.usefixtures("admin")
async def test_cannot_create_a_room_of_a_retired_type(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    old = await make_type(db_session, "OLD", "Old wing", active=False)

    response = await client.post(ROOMS, json={"number": "1", "floor": 1, "room_type_id": old.id})

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Room type OLD is retired. Choose an active room type.",
        "code": "room_type_retired",
    }


@pytest.mark.usefixtures("admin")
@pytest.mark.parametrize(
    "body",
    [
        {"number": "", "floor": 1},
        {"number": "1 01", "floor": 1},
        {"number": "12345678901", "floor": 1},
        {"number": "101", "floor": -1},
        {"number": "101", "floor": 201},
        {"number": "101"},
        {"number": "101", "floor": 1, "housekeeping_status": "dirty"},
    ],
)
async def test_create_room_validation(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    std = await make_type(db_session)

    assert (await client.post(ROOMS, json={**body, "room_type_id": std.id})).status_code == 422


# --- rooms: editing ---------------------------------------------------------------------


@pytest.mark.usefixtures("admin")
async def test_update_room(
    client: AsyncClient, db_session: AsyncSession, published: list[DomainEvent]
) -> None:
    std = await make_type(db_session, "STD", "Standard")
    dlx = await make_type(db_session, "DLX", "Deluxe")
    room = await make_room(db_session, "101", std)

    response = await client.patch(
        f"{ROOMS}/{room.id}",
        json={"version": 1, "number": "101a", "floor": 2, "room_type_id": dlx.id, "notes": "x"},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert (data["number"], data["floor"], data["room_type"]["code"], data["version"]) == (
        "101A",
        2,
        "DLX",
        2,
    )
    [row] = await audit_rows(db_session, "room.updated")
    assert row.before == {
        "number": "101",
        "floor": 1,
        "room_type_id": std.id,
        "notes": None,
        "room_type_code": "STD",
    }
    assert row.after == {
        "number": "101A",
        "floor": 2,
        "room_type_id": dlx.id,
        "notes": "x",
        "room_type_code": "DLX",
    }
    assert len(setup_events(published)) == 1


@pytest.mark.usefixtures("admin")
async def test_stale_version_is_a_conflict(client: AsyncClient, db_session: AsyncSession) -> None:
    room = await make_room(db_session, "101", await make_type(db_session))
    first = await client.patch(f"{ROOMS}/{room.id}", json={"version": 1, "floor": 2})

    second = await client.patch(f"{ROOMS}/{room.id}", json={"version": 1, "floor": 3})
    retire = await client.post(f"{ROOMS}/{room.id}/retire", json={"version": 1})

    assert first.status_code == 200
    for response in (second, retire):
        assert response.status_code == 409
        assert response.json() == {
            "detail": "Room 101 was changed by someone else. Reload and try again.",
            "code": "version_conflict",
        }


@pytest.mark.usefixtures("admin")
async def test_room_type_cannot_change_while_a_guest_is_checked_in(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    std = await make_type(db_session, "STD", "Standard")
    dlx = await make_type(db_session, "DLX", "Deluxe")
    room = await make_room(db_session, "304", std)
    await book(db_session, std, room=room, status=ReservationStatus.CHECKED_IN, nights=(-1, 2))

    blocked = await client.patch(f"{ROOMS}/{room.id}", json={"version": 1, "room_type_id": dlx.id})
    notes_ok = await client.patch(f"{ROOMS}/{room.id}", json={"version": 1, "notes": "Quiet"})

    assert blocked.status_code == 409
    assert blocked.json() == {
        "detail": "Room 304 has a guest checked in, so its room type cannot change. "
        "Try again after check-out.",
        "code": "room_in_use",
    }
    assert notes_ok.status_code == 200  # other edits are fine


@pytest.mark.usefixtures("admin")
async def test_cannot_move_a_room_to_a_retired_type(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    room = await make_room(db_session, "101", await make_type(db_session))
    old = await make_type(db_session, "OLD", "Old wing", active=False)

    response = await client.patch(f"{ROOMS}/{room.id}", json={"version": 1, "room_type_id": old.id})

    assert response.status_code == 409
    assert response.json()["code"] == "room_type_retired"


@pytest.mark.usefixtures("admin")
async def test_renumbering_onto_an_existing_room(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    std = await make_type(db_session)
    await make_room(db_session, "101", std)
    room = await make_room(db_session, "102", std)

    response = await client.patch(f"{ROOMS}/{room.id}", json={"version": 1, "number": "101"})

    assert response.status_code == 409
    assert response.json()["code"] == "room_number_taken"


@pytest.mark.usefixtures("admin")
@pytest.mark.parametrize(
    "body",
    [{}, {"version": 0}, {"version": 1, "number": None}, {"version": 1, "floor": None}],
)
async def test_update_room_validation(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    room = await make_room(db_session, "101", await make_type(db_session))

    assert (await client.patch(f"{ROOMS}/{room.id}", json=body)).status_code == 422


@pytest.mark.usefixtures("admin")
async def test_no_op_room_update_records_nothing(
    client: AsyncClient, db_session: AsyncSession, published: list[DomainEvent]
) -> None:
    room = await make_room(db_session, "101", await make_type(db_session))

    response = await client.patch(f"{ROOMS}/{room.id}", json={"version": 1, "number": "101"})

    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert await audit_rows(db_session, "room.updated") == []
    assert setup_events(published) == []


# --- rooms: retiring ------------------------------------------------------------------


@pytest.mark.usefixtures("admin")
async def test_room_with_a_guest_checked_in_cannot_be_retired(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    std = await make_type(db_session)
    room = await make_room(db_session, "304", std)
    await book(db_session, std, room=room, status=ReservationStatus.CHECKED_IN, nights=(-1, 2))

    response = await client.post(f"{ROOMS}/{room.id}/retire", json={"version": 1})

    assert response.status_code == 409
    assert response.json() == {"detail": "Room 304 has a guest checked in.", "code": "room_in_use"}


@pytest.mark.usefixtures("admin")
@pytest.mark.parametrize(
    ("status", "nights", "blocks"),
    [
        (ReservationStatus.CONFIRMED, (3, 5), True),
        (ReservationStatus.CONFIRMED, (0, 2), True),  # arriving today
        (ReservationStatus.CONFIRMED, (-2, 0), False),  # ended today (no-show to settle)
        (ReservationStatus.CHECKED_OUT, (-3, -1), False),
        (ReservationStatus.CANCELLED, (3, 5), False),
    ],
)
async def test_room_with_upcoming_assigned_reservations_cannot_be_retired(
    client: AsyncClient,
    db_session: AsyncSession,
    status: ReservationStatus,
    nights: tuple[int, int],
    blocks: bool,
) -> None:
    std = await make_type(db_session)
    room = await make_room(db_session, "304", std)
    await book(db_session, std, room=room, status=status, nights=nights)

    response = await client.post(f"{ROOMS}/{room.id}/retire", json={"version": 1})

    assert response.status_code == (409 if blocks else 200), response.text
    if blocks:
        assert response.json()["detail"] == (
            "Room 304 has 1 upcoming reservation assigned. Move them to another room first."
        )


@pytest.mark.usefixtures("admin")
async def test_retire_and_reactivate_a_room(
    client: AsyncClient,
    db_session: AsyncSession,
    published: list[DomainEvent],
    login_as: LoginAs,
) -> None:
    std = await make_type(db_session)
    room = await make_room(db_session, "101", std)
    await make_room(db_session, "102", std)

    retired = await client.post(f"{ROOMS}/{room.id}/retire", json={"version": 1})

    assert retired.status_code == 200, retired.text
    data = retired.json()
    assert (data["is_active"], data["version"]) == (False, 2)
    assert data["retired_at"] is not None
    assert data["retired_by"]["username"] == "admin"
    [row] = await audit_rows(db_session, "room.retired")
    assert row.before == {"is_active": True}
    assert row.after is not None and row.after["number"] == "101"
    # Gone from the front desk: no tile, not counted, no actions.
    dashboard = (await client.get("/api/dashboard/rooms")).json()["rooms"]
    assert [r["number"] for r in dashboard] == ["102"]
    summary = (await client.get("/api/dashboard/summary")).json()
    assert summary["available_tonight"] == 1
    housekeeping = await client.patch(
        f"/api/rooms/{room.id}/housekeeping-status", json={"status": "dirty", "version": 2}
    )
    assert housekeeping.status_code == 409
    assert housekeeping.json()["detail"] == "Room 101 is retired."
    # Retiring twice is refused; reactivating brings it back.
    assert (await client.post(f"{ROOMS}/{room.id}/retire", json={"version": 2})).json()[
        "code"
    ] == "invalid_transition"
    back = await client.post(f"{ROOMS}/{room.id}/reactivate", json={"version": 2})
    assert back.status_code == 200
    assert (back.json()["is_active"], back.json()["retired_at"], back.json()["retired_by"]) == (
        True,
        None,
        None,
    )
    assert len(await audit_rows(db_session, "room.reactivated")) == 1
    assert len(setup_events(published)) == 2


@pytest.mark.usefixtures("admin")
async def test_a_room_of_a_retired_type_cannot_be_reactivated(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    old = await make_type(db_session, "OLD", "Old wing")
    room = await make_room(db_session, "101", old)
    room.is_active, room.retired_at = False, TODAY_DT
    old.is_active = False
    await db_session.flush()

    response = await client.post(f"{ROOMS}/{room.id}/reactivate", json={"version": room.version})

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Room 101 uses the retired room type OLD. Reactivate the room type first, "
        "or give the room another type.",
        "code": "room_type_retired",
    }


@pytest.mark.usefixtures("admin")
async def test_unknown_room(client: AsyncClient) -> None:
    for response in (
        await client.patch(f"{ROOMS}/999999", json={"version": 1, "floor": 1}),
        await client.post(f"{ROOMS}/999999/retire", json={"version": 1}),
        await client.post(f"{ROOMS}/999999/reactivate", json={"version": 1}),
    ):
        assert response.status_code == 404


# --- permissions and audit -------------------------------------------------------------


async def test_general_manager_without_room_manage_is_refused(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.GENERAL_MANAGER)
    std = await make_type(db_session)
    room = await make_room(db_session, "101", std)

    for method, path, body in [
        ("GET", TYPES, None),
        ("POST", TYPES, {"code": "NEW", "name": "New", "max_occupancy": 2}),
        ("PATCH", f"{TYPES}/{std.id}", {"name": "Renamed"}),
        ("POST", f"{TYPES}/{std.id}/retire", None),
        ("POST", f"{TYPES}/{std.id}/reactivate", None),
        ("GET", ROOMS, None),
        ("POST", ROOMS, {"number": "102", "floor": 1, "room_type_id": std.id}),
        ("PATCH", f"{ROOMS}/{room.id}", {"version": 1, "floor": 2}),
        ("POST", f"{ROOMS}/{room.id}/retire", {"version": 1}),
        ("POST", f"{ROOMS}/{room.id}/reactivate", {"version": 1}),
    ]:
        response = await client.request(method, path, json=body)
        assert response.status_code == 403, (method, path)
    assert (
        await db_session.scalar(select(AuditLog.id).where(AuditLog.target_type == "room")) is None
    )


@pytest.mark.usefixtures("admin")
async def test_changes_appear_in_the_audit_log_with_names(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    created = (
        await client.post(TYPES, json={"code": "FAM", "name": "Family", "max_occupancy": 4})
    ).json()
    room = (
        await client.post(ROOMS, json={"number": "501", "floor": 5, "room_type_id": created["id"]})
    ).json()
    await client.post(f"{ROOMS}/{room['id']}/retire", json={"version": 1})

    entries = (await client.get("/api/admin/audit", params={"limit": 10})).json()["items"]

    trail = [(e["action"], e["target_type"], e["target_label"]) for e in entries][:3]
    assert trail == [
        ("room.retired", "room", "501"),
        ("room.created", "room", "501"),
        ("room_type.created", "room_type", "FAM Family"),
    ]
    filtered = await client.get("/api/admin/audit", params={"target_type": "room_type"})
    assert [e["action"] for e in filtered.json()["items"]] == ["room_type.created"]
