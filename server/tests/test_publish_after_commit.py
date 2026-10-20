"""Every reservation write publishes its events only after its transaction committed.

A subscriber reads the reservation (and room) from a separate connection when the event
arrives. A separate connection only sees committed data, so an event published before
commit would see the old row (or none) and this test would fail.
"""

import uuid
from datetime import timedelta

from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import HousekeepingStatus, Reservation, Room
from app.realtime.events import DomainEvent, ReservationChanged, RoomStatusChanged
from tests.conftest import Roles, live_clients
from tests.test_realtime import TODAY, live, seeded
from tests.test_reservation_concurrency import one_room_left

__all__ = ["seeded"]


def ok(response: Response) -> dict[str, object]:
    assert response.status_code in (200, 201), response.text
    body: dict[str, object] = response.json()
    return body


async def test_events_see_committed_data_for_every_write(
    test_settings: Settings,
    seeded: dict[str, int],
    live_db: async_sessionmaker[AsyncSession],
) -> None:
    room_type_id, guest_id = await one_room_left(live_db)  # LST: rooms 801 and 802
    async with live_db() as db:
        found = list(await db.scalars(select(Room).where(Room.number.in_(["801", "802"]))))
        for room in found:
            room.housekeeping_status = HousekeepingStatus.INSPECTED  # ready for a walk-in
        await db.commit()
        rooms = {room.number: room.id for room in found}

    seen: list[str] = []
    problems: list[str] = []

    async with live_clients(test_settings) as make:
        app, desk = await live(make, Roles.FRONT_OFFICE_MANAGER)

        async def check(event: DomainEvent) -> None:
            async with app.state.session_factory() as db:  # its own connection
                if isinstance(event, ReservationChanged):
                    row = await db.get(Reservation, event.reservation_id)
                    seen.append(event.change)
                    if row is None or row.status != event.status:
                        problems.append(f"{event.change}: database has {row and row.status}")
                    elif (row.check_in_date, row.check_out_date) != (
                        event.check_in_date,
                        event.check_out_date,
                    ):
                        problems.append(f"{event.change}: dates not committed yet")
                elif isinstance(event, RoomStatusChanged):
                    room = await db.get(Room, event.room.room_id)
                    if room is None or room.version != event.room.version:
                        problems.append(f"room {event.room.number}: version not committed yet")

        app.state.event_bus.subscribe(check)
        later = (TODAY + timedelta(days=5)).isoformat(), (TODAY + timedelta(days=7)).isoformat()

        # Book (future), change, assign and unassign a room, cancel.
        booking = ok(
            await desk.post(
                "/api/reservations",
                headers={"Idempotency-Key": uuid.uuid4().hex},
                json={
                    "guest_id": guest_id,
                    "room_type_id": room_type_id,
                    "check_in_date": later[0],
                    "check_out_date": later[1],
                    "source": "phone",
                },
            )
        )
        rid = booking["id"]
        ok(await desk.patch(f"/api/reservations/{rid}", json={"version": 1, "notes": "Cot"}))
        ok(
            await desk.post(
                f"/api/reservations/{rid}/assign-room",
                json={"version": 2, "room_id": rooms["802"]},
            )
        )
        ok(await desk.post(f"/api/reservations/{rid}/unassign-room", json={"version": 3}))
        ok(await desk.post(f"/api/reservations/{rid}/cancel", json={"version": 4, "reason": "x"}))

        # A walk-in tonight in room 801, then check-out.
        walk_in = ok(
            await desk.post(
                "/api/reservations/walk-in",
                headers={"Idempotency-Key": uuid.uuid4().hex},
                json={
                    "guest_id": guest_id,
                    "room_id": rooms["801"],
                    "room_version": 2,  # bumped by the housekeeping change above
                    "check_out_date": (TODAY + timedelta(days=1)).isoformat(),
                },
            )
        )
        tiles = (await desk.get("/api/dashboard/rooms")).json()["rooms"]
        tile = next(r for r in tiles if r["room_id"] == rooms["801"])
        ok(
            await desk.post(
                f"/api/reservations/{walk_in['id']}/check-out",
                json={"room_version": tile["version"]},
            )
        )

        # A booking for another reservation's no-show path.
        no_show = ok(
            await desk.post(
                "/api/reservations",
                headers={"Idempotency-Key": uuid.uuid4().hex},
                json={
                    "guest_id": guest_id,
                    "room_type_id": room_type_id,
                    "check_in_date": TODAY.isoformat(),
                    "check_out_date": (TODAY + timedelta(days=1)).isoformat(),
                    "source": "phone",
                },
            )
        )
        ok(await desk.post(f"/api/reservations/{no_show['id']}/no-show", json={"version": 1}))

    assert problems == []
    assert seen == [
        "created",
        "updated",
        "room_assigned",
        "room_unassigned",
        "cancelled",
        "checked_in",
        "checked_out",
        "created",
        "no_show",
    ]
