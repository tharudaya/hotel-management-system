from datetime import date

import pytest

from app.models import HousekeepingStatus
from app.realtime.bus import EventBus
from app.realtime.events import DomainEvent, RoomStatusChanged
from app.schemas.dashboard import DashboardRoom, OccupancyStatus

EVENT: RoomStatusChanged = RoomStatusChanged(
    business_date=date(2026, 3, 10),
    room=DashboardRoom(
        room_id=1,
        number="101",
        floor=1,
        room_type="STD",
        room_type_name="Standard",
        housekeeping_status=HousekeepingStatus.CLEAN,
        occupancy_status=OccupancyStatus.VACANT,
        guest_name=None,
        reservation_id=None,
        nights_remaining=None,
        is_vip=False,
        has_notes=False,
        has_arrival_today=False,
        version=2,
    ),
)


async def test_publish_delivers_to_all_subscribers_in_order() -> None:
    bus = EventBus()
    received: list[tuple[str, DomainEvent]] = []

    async def first(event: DomainEvent) -> None:
        received.append(("first", event))

    async def second(event: DomainEvent) -> None:
        received.append(("second", event))

    bus.subscribe(first)
    bus.subscribe(second)
    await bus.publish(EVENT)

    assert received == [("first", EVENT), ("second", EVENT)]


async def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    received: list[DomainEvent] = []

    async def handler(event: DomainEvent) -> None:
        received.append(event)

    unsubscribe = bus.subscribe(handler)
    unsubscribe()
    unsubscribe()  # idempotent
    await bus.publish(EVENT)

    assert received == []


async def test_failing_handler_does_not_block_others(caplog: pytest.LogCaptureFixture) -> None:
    bus = EventBus()
    received: list[DomainEvent] = []

    async def broken(event: DomainEvent) -> None:
        raise RuntimeError("boom")

    async def healthy(event: DomainEvent) -> None:
        received.append(event)

    bus.subscribe(broken)
    bus.subscribe(healthy)
    await bus.publish(EVENT)

    assert received == [EVENT]
    assert "room_status_changed" in caplog.text


def test_event_serializes_for_the_wire() -> None:
    payload = EVENT.model_dump(mode="json")

    assert payload["type"] == "room_status_changed"
    assert payload["business_date"] == "2026-03-10"
    assert payload["room"]["number"] == "101"
