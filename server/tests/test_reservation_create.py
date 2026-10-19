"""POST /api/reservations: booking, rates, validation, idempotency, availability, events."""

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
    IdempotencyKey,
    Reservation,
    ReservationNight,
    Room,
    RoomType,
)
from app.realtime.events import DomainEvent, ReservationChanged, SummaryChanged
from app.services import reservations as reservation_module
from app.services.permissions import get_default_property_id
from tests.conftest import LoginAs, MakeUser, Roles
from tests.factories import new_guest, new_reservation
from tests.test_guests_api import login_without_contact_access

TODAY: date = date(2026, 3, 10)


def day(offset: int) -> date:
    return TODAY + timedelta(days=offset)


@dataclass
class Hotel:
    property_id: int
    std: RoomType  # 2 rooms, base rate 1 000.00, up to 2 guests
    ste: RoomType  # 1 room, no base rate set, up to 4 guests
    guest: Guest


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
        property_id=pid, code="STD", name="Standard", max_occupancy=2, base_rate_minor=100_000
    )
    ste = RoomType(property_id=pid, code="STE", name="Suite", max_occupancy=4)
    guest = new_guest(pid, "Ada", "Lovelace", email="ada@example.com")
    db_session.add_all(
        [
            Room(property_id=pid, number="101", floor=1, room_type=std),
            Room(property_id=pid, number="102", floor=1, room_type=std),
            Room(property_id=pid, number="301", floor=3, room_type=ste),
            guest,
        ]
    )
    await db_session.flush()
    return Hotel(property_id=pid, std=std, ste=ste, guest=guest)


def booking(hotel: Hotel, **overrides: object) -> dict[str, Any]:
    body: dict[str, Any] = {
        "guest_id": hotel.guest.id,
        "room_type_id": hotel.std.id,
        "check_in_date": day(0).isoformat(),
        "check_out_date": day(2).isoformat(),
        "adults": 2,
        "children": 0,
        "source": "phone",
    }
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not None or k in overrides}


async def book(client: AsyncClient, body: dict[str, Any], key: str | None = None) -> Response:
    return await client.post(
        "/api/reservations",
        json=body,
        headers={"Idempotency-Key": key or uuid.uuid4().hex},
    )


async def count(db: AsyncSession, model: type[Any]) -> int:
    return await db.scalar(select(func.count()).select_from(model)) or 0


# --- happy path --------------------------------------------------------------------------


async def test_book_for_an_existing_guest(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    published: list[DomainEvent],
) -> None:
    user = await login_as(Roles.RECEPTIONIST)

    response = await book(client, booking(hotel, notes=" Late arrival ", is_vip=True))

    assert response.status_code == 201, response.text
    assert "Idempotent-Replayed" not in response.headers
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["source"] == "phone"
    assert len(body["confirmation_code"]) == 8
    assert (body["room_id"], body["room_type"], body["nights"]) == (None, "STD", 2)
    assert (body["notes"], body["is_vip"], body["version"]) == ("Late arrival", True, 1)
    assert body["nightly_rates"] == [
        {"night_date": "2026-03-10", "rate_minor": 100_000},
        {"night_date": "2026-03-11", "rate_minor": 100_000},
    ]
    assert (body["total_minor"], body["currency"]) == (200_000, "SEK")
    assert body["guest"]["id"] == hotel.guest.id
    assert body["guest"]["email"] == "ada@example.com"  # receptionists see contact details

    reservation = await db_session.get_one(Reservation, body["id"])
    assert reservation.created_by == user.id
    [audit] = (
        await db_session.scalars(select(AuditLog).where(AuditLog.action == "reservation.created"))
    ).all()
    assert (audit.target_type, audit.target_id, audit.actor_user_id) == (
        "reservation",
        str(body["id"]),
        user.id,
    )
    assert audit.after is not None
    assert audit.after["confirmation_code"] == body["confirmation_code"]
    assert audit.after["total_minor"] == 200_000
    assert audit.after["rate_overridden"] is False
    assert "ada@example.com" not in str(audit.after)

    reservation_event, summary_event = published
    assert reservation_event == ReservationChanged(
        property_id=hotel.property_id,
        reservation_id=body["id"],
        change="created",
        status="confirmed",
        room_type_id=hotel.std.id,
        check_in_date=day(0),
        check_out_date=day(2),
    )
    # Arriving today: the dashboard's arrival count changed.
    assert isinstance(summary_event, SummaryChanged)
    assert summary_event.summary.arrivals_today == 1


async def test_future_booking_publishes_no_summary(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, published: list[DomainEvent]
) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await book(
        client, booking(hotel, check_in_date=str(day(5)), check_out_date=str(day(6)))
    )

    assert response.status_code == 201
    assert [type(e) for e in published] == [ReservationChanged]


async def test_book_for_a_new_guest(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    guests_before = await count(db_session, Guest)

    response = await book(
        client,
        booking(
            hotel,
            guest_id=None,
            guest={"first_name": " Bo ", "last_name": "Berg", "phone": "+46 70 111 22 33"},
            source="walk_in",
        ),
    )

    assert response.status_code == 201, response.text
    guest = response.json()["guest"]
    assert (guest["first_name"], guest["last_name"], guest["phone"]) == (
        "Bo",
        "Berg",
        "+46701112233",
    )
    assert await count(db_session, Guest) == guests_before + 1
    actions = (await db_session.scalars(select(AuditLog.action).order_by(AuditLog.id))).all()
    assert actions == ["guest.created", "reservation.created"]


# --- rates -------------------------------------------------------------------------------


@pytest.mark.parametrize("role", [Roles.FRONT_OFFICE_MANAGER, Roles.GENERAL_MANAGER])
async def test_managers_can_override_the_rate(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession, role: str
) -> None:
    await login_as(role)

    response = await book(client, booking(hotel, nightly_rate_minor=85_000))

    assert response.status_code == 201, response.text
    body = response.json()
    assert [n["rate_minor"] for n in body["nightly_rates"]] == [85_000, 85_000]
    assert body["total_minor"] == 170_000
    audit = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "reservation.created")
    )
    assert audit is not None and audit.after is not None
    assert (audit.after["rate_overridden"], audit.after["nightly_rate_minor"]) == (True, 85_000)


async def test_receptionist_cannot_override_the_rate(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await book(client, booking(hotel, nightly_rate_minor=1))

    assert response.status_code == 403
    assert response.json()["code"] == "rate_override_not_allowed"
    assert await count(db_session, Reservation) == 0


async def test_sending_the_base_rate_is_not_an_override(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel
) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await book(client, booking(hotel, nightly_rate_minor=100_000))

    assert response.status_code == 201


async def test_room_type_without_a_rate_needs_a_price(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel
) -> None:
    await login_as(Roles.RECEPTIONIST)
    no_rate = await book(client, booking(hotel, room_type_id=hotel.ste.id))
    assert no_rate.status_code == 409
    assert no_rate.json()["code"] == "rate_not_set"

    await login_as(Roles.FRONT_OFFICE_MANAGER)
    priced = await book(client, booking(hotel, room_type_id=hotel.ste.id, nightly_rate_minor=0))
    assert priced.status_code == 201, priced.text
    assert priced.json()["total_minor"] == 0


# --- validation --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"check_in_date": str(day(-1))}, "check_in_in_past"),
        ({"check_out_date": str(day(0))}, "invalid_dates"),
        ({"check_out_date": str(day(31))}, "stay_too_long"),
        (
            {"check_in_date": str(day(731)), "check_out_date": str(day(732))},
            "beyond_booking_horizon",
        ),
        ({"adults": 2, "children": 1}, "too_many_guests"),
        ({"guest_id": 999999}, "guest_not_found"),
    ],
)
async def test_bookings_the_hotel_does_not_take(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    overrides: dict[str, Any],
    code: str,
) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await book(client, booking(hotel, **overrides))

    assert response.status_code == 422, response.text
    assert response.json()["code"] == code
    assert await count(db_session, Reservation) == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"guest_id": None},  # neither guest_id nor guest
        {"guest": {"first_name": "A", "last_name": "B"}},  # both
        {"adults": 0},
        {"children": -1},
        {"source": "website"},
        {"check_in_date": "soon"},
        {"room_type_id": 0},
        {"nightly_rate_minor": -1},
        {"notes": "x" * 2001},
        {"status": "checked_in"},
        {"guest_id": None, "guest": {"first_name": "A", "last_name": "B", "email": "x"}},
    ],
)
async def test_booking_body_validation(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, overrides: dict[str, Any]
) -> None:
    await login_as(Roles.RECEPTIONIST)

    assert (await book(client, booking(hotel, **overrides))).status_code == 422


@pytest.mark.parametrize(
    "headers",
    [{}, {"Idempotency-Key": "short"}, {"Idempotency-Key": "no spaces allowed"}],
)
async def test_idempotency_key_header_is_required(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, headers: dict[str, str]
) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await client.post("/api/reservations", json=booking(hotel), headers=headers)

    assert response.status_code == 422


async def test_unknown_and_retired_room_types(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    unknown = await book(client, booking(hotel, room_type_id=999999))
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "room_type_not_found"

    retired = RoomType(
        property_id=hotel.property_id,
        code="OLD",
        name="Old",
        max_occupancy=2,
        base_rate_minor=1,
        is_active=False,
    )
    db_session.add(retired)
    await db_session.flush()
    response = await book(client, booking(hotel, room_type_id=retired.id))
    assert response.status_code == 409
    assert response.json()["code"] == "room_type_retired"


# --- availability ------------------------------------------------------------------------


async def test_full_nights_are_refused_and_listed(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    # Both Standard rooms are taken on day 1; one on day 2.
    for check_in, check_out in [(1, 2), (1, 3)]:
        db_session.add(
            new_reservation(hotel.std, check_in_date=day(check_in), check_out_date=day(check_out))
        )
    await db_session.flush()
    await login_as(Roles.RECEPTIONIST)

    response = await book(
        client, booking(hotel, check_in_date=str(day(0)), check_out_date=str(day(3)))
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "no_availability"
    assert body["unavailable_nights"] == ["2026-03-11"]
    assert body["detail"] == "No Standard room is free on 2026-03-11."
    # The same stay without that night works.
    assert (
        await book(client, booking(hotel, check_in_date=str(day(2)), check_out_date=str(day(3))))
    ).status_code == 201


async def test_the_last_room_can_be_sold_once(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel
) -> None:
    await login_as(Roles.RECEPTIONIST)

    first, second, third = [await book(client, booking(hotel)) for _ in range(3)]

    assert (first.status_code, second.status_code, third.status_code) == (201, 201, 409)


# --- idempotency -------------------------------------------------------------------------


async def test_same_key_and_body_returns_the_original(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    published: list[DomainEvent],
) -> None:
    await login_as(Roles.RECEPTIONIST)
    key = uuid.uuid4().hex

    first = await book(client, booking(hotel), key)
    # Same request, keys in another order: still the same request.
    retry = await book(client, dict(reversed(list(booking(hotel).items()))), key)

    assert (first.status_code, retry.status_code) == (201, 201)
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.json() == first.json()
    assert await count(db_session, Reservation) == 1
    assert await count(db_session, ReservationNight) == 2
    assert await count(db_session, IdempotencyKey) == 1
    assert len([e for e in published if isinstance(e, ReservationChanged)]) == 1


async def test_same_key_with_a_different_body_is_refused(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_as(Roles.RECEPTIONIST)
    key = uuid.uuid4().hex
    assert (await book(client, booking(hotel), key)).status_code == 201

    response = await book(client, booking(hotel, adults=1), key)

    assert response.status_code == 422
    assert response.json()["code"] == "idempotency_key_reused"
    assert await count(db_session, Reservation) == 1


async def test_keys_belong_to_one_user(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, db_session: AsyncSession
) -> None:
    key = uuid.uuid4().hex
    await login_as(Roles.RECEPTIONIST)
    assert (await book(client, booking(hotel), key)).status_code == 201

    await login_as(Roles.FRONT_OFFICE_MANAGER)
    response = await book(client, booking(hotel), key)

    assert response.status_code == 201
    assert "Idempotent-Replayed" not in response.headers
    assert await count(db_session, Reservation) == 2


async def test_failed_request_does_not_use_up_the_key(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel
) -> None:
    await login_as(Roles.RECEPTIONIST)
    key = uuid.uuid4().hex
    refused = await book(client, booking(hotel, check_in_date=str(day(-1))), key)
    assert refused.status_code == 422

    assert (await book(client, booking(hotel), key)).status_code == 201


# --- confirmation codes ------------------------------------------------------------------


async def test_code_collision_is_retried(
    client: AsyncClient,
    login_as: LoginAs,
    hotel: Hotel,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    taken = new_reservation(
        hotel.std, check_in_date=day(5), check_out_date=day(6), confirmation_code="TAKEN234"
    )
    db_session.add(taken)
    await db_session.flush()
    codes = iter(["TAKEN234", "TAKEN234", "FRESH234"])
    monkeypatch.setattr(reservation_module, "new_confirmation_code", lambda: next(codes))
    await login_as(Roles.RECEPTIONIST)

    response = await book(client, booking(hotel))

    assert response.status_code == 201, response.text
    assert response.json()["confirmation_code"] == "FRESH234"
    assert await count(db_session, Reservation) == 2


# --- permissions -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "status"),
    [
        (Roles.RECEPTIONIST, 201),
        (Roles.FRONT_OFFICE_MANAGER, 201),
        (Roles.HOUSEKEEPING_SUPERVISOR, 403),
        (Roles.ROOM_ATTENDANT, 403),
    ],
)
async def test_booking_needs_reservation_create(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel, role: str, status: int
) -> None:
    await login_as(role)

    assert (await book(client, booking(hotel))).status_code == status


async def test_booking_requires_login(client: AsyncClient, hotel: Hotel) -> None:
    assert (await book(client, booking(hotel))).status_code == 401


async def test_contact_details_follow_the_bookers_permissions(
    client: AsyncClient, make_user: MakeUser, hotel: Hotel, db_session: AsyncSession
) -> None:
    await login_without_contact_access(client, make_user, db_session)

    response = await book(client, booking(hotel))

    assert response.status_code == 201, response.text
    guest = response.json()["guest"]
    assert (guest["email"], guest["contact_hidden"]) == (None, True)


# --- audit log and cleanup ---------------------------------------------------------------


async def test_audit_log_names_the_reservation(
    client: AsyncClient, login_as: LoginAs, hotel: Hotel
) -> None:
    await login_as(Roles.ADMIN)
    created = (await book(client, booking(hotel))).json()

    entries = (await client.get("/api/admin/audit", params={"target_type": "reservation"})).json()

    [entry] = entries["items"]
    assert (entry["action"], entry["target_label"]) == (
        "reservation.created",
        created["confirmation_code"],
    )


async def test_old_idempotency_keys_are_deleted(
    db_session: AsyncSession, login_as: LoginAs
) -> None:
    from datetime import UTC, datetime

    from app.services import idempotency

    user = await login_as(Roles.RECEPTIONIST)
    now = datetime(2026, 3, 10, 12, tzinfo=UTC)
    for key, age in [("old-key-1", 25), ("new-key-1", 23)]:
        record = idempotency.new_record(
            key=key,
            user_id=user.id,
            endpoint="POST /api/reservations",
            body_hash="0" * 64,
            reference="reservation:1",
        )
        record.created_at = now - timedelta(hours=age)
        db_session.add(record)
    await db_session.flush()

    assert await idempotency.delete_expired(db_session, now=now) == 1

    keys = (await db_session.scalars(select(IdempotencyKey.key))).all()
    assert keys == ["new-key-1"]
