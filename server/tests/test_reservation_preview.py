"""Previewing a change (nothing saved) and availability that leaves a booking out."""

from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hotel_today
from app.models import AuditLog, Reservation, ReservationNight, Room, RoomType
from app.realtime.events import DomainEvent
from app.services.permissions import get_default_property_id
from tests.conftest import LoginAs, Roles
from tests.factories import new_reservation

TODAY: date = date(2026, 3, 10)


def day(offset: int) -> date:
    return TODAY + timedelta(days=offset)


@pytest.fixture
def published(app: FastAPI) -> list[DomainEvent]:
    events: list[DomainEvent] = []

    async def record(event: DomainEvent) -> None:
        events.append(event)

    app.state.event_bus.subscribe(record)
    return events


@pytest.fixture
async def booking(db_session: AsyncSession, app: FastAPI) -> Reservation:
    """The only Standard room is booked days 2-4 by this reservation, at 90 000/night."""
    app.dependency_overrides[get_hotel_today] = lambda: TODAY
    pid = await get_default_property_id(db_session)
    std = RoomType(
        property_id=pid, code="STD", name="Standard", max_occupancy=2, base_rate_minor=100_000
    )
    db_session.add(Room(property_id=pid, number="101", floor=1, room_type=std))
    reservation = new_reservation(
        std, check_in_date=day(2), check_out_date=day(4), rate_minor=90_000
    )
    db_session.add(reservation)
    await db_session.flush()
    return reservation


async def preview(client: AsyncClient, r: Reservation, **body: object) -> dict[str, object]:
    response = await client.post(
        f"/api/reservations/{r.id}/preview-change", json={"version": 1, **body}
    )
    assert response.status_code == 200, response.text
    result: dict[str, object] = response.json()
    return result


async def test_preview_shows_the_new_prices_and_saves_nothing(
    client: AsyncClient,
    login_as: LoginAs,
    booking: Reservation,
    db_session: AsyncSession,
    published: list[DomainEvent],
) -> None:
    await login_as(Roles.RECEPTIONIST)

    body = await preview(client, booking, check_out_date=str(day(5)))

    assert body["check_out_date"] == "2026-03-15"
    assert body["nightly_rates"] == [
        {"night_date": "2026-03-12", "rate_minor": 90_000},
        {"night_date": "2026-03-13", "rate_minor": 90_000},
        {"night_date": "2026-03-14", "rate_minor": 100_000},
    ]
    assert (body["total_minor"], body["version"], body["history"]) == (280_000, 1, [])
    # Nothing changed.
    await db_session.refresh(booking)
    assert (booking.check_out_date, booking.total_minor, booking.version) == (day(4), 180_000, 1)
    nights = await db_session.scalar(
        select(func.count())
        .select_from(ReservationNight)
        .where(ReservationNight.reservation_id == booking.id)
    )
    assert nights == 2
    assert (await db_session.scalars(select(AuditLog))).all() == []
    assert published == []
    # And the real change still works with the same version.
    saved = await client.patch(
        f"/api/reservations/{booking.id}", json={"version": 1, "check_out_date": str(day(5))}
    )
    assert saved.status_code == 200
    assert saved.json()["total_minor"] == 280_000


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"check_out_date": "2026-03-12"}, 422, "invalid_dates"),
        ({"nightly_rate_minor": 1}, 403, "rate_override_not_allowed"),
        ({"version": 7, "notes": "x"}, 409, "version_conflict"),
    ],
)
async def test_preview_gives_the_same_refusals_as_saving(
    client: AsyncClient,
    login_as: LoginAs,
    booking: Reservation,
    body: dict[str, object],
    status: int,
    code: str,
) -> None:
    await login_as(Roles.RECEPTIONIST)

    response = await client.post(
        f"/api/reservations/{booking.id}/preview-change", json={"version": 1, **body}
    )

    assert response.status_code == status
    assert response.json()["code"] == code


async def test_preview_of_full_nights(
    client: AsyncClient, login_as: LoginAs, booking: Reservation, db_session: AsyncSession
) -> None:
    room_type = await db_session.get_one(RoomType, booking.room_type_id)
    db_session.add(new_reservation(room_type, check_in_date=day(4), check_out_date=day(6)))
    await db_session.flush()
    await login_as(Roles.RECEPTIONIST)

    response = await client.post(
        f"/api/reservations/{booking.id}/preview-change",
        json={"version": 1, "check_out_date": str(day(5))},
    )

    assert response.status_code == 409
    assert response.json()["unavailable_nights"] == ["2026-03-14"]


async def test_preview_needs_reservation_modify(
    client: AsyncClient, login_as: LoginAs, booking: Reservation
) -> None:
    await login_as(Roles.HOUSEKEEPING_SUPERVISOR)

    response = await client.post(
        f"/api/reservations/{booking.id}/preview-change", json={"version": 1, "notes": "x"}
    )

    assert response.status_code == 403


async def test_availability_can_leave_the_booking_being_changed_out(
    client: AsyncClient, login_as: LoginAs, booking: Reservation
) -> None:
    await login_as(Roles.RECEPTIONIST)
    params = {"check_in": str(day(2)), "check_out": str(day(4))}

    taken = await client.get("/api/reservations/availability", params=params)
    excluded = await client.get(
        "/api/reservations/availability",
        params={**params, "exclude_reservation_id": str(booking.id)},
    )

    assert taken.json()["room_types"][0]["available"] == 0
    assert excluded.json()["room_types"][0]["available"] == 1
