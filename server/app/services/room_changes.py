"""Helpers shared by actions that change a room's dashboard state."""

from collections.abc import Collection
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.models import Room
from app.realtime.bus import EventBus
from app.realtime.events import RoomStatusChanged, SummaryChanged
from app.schemas.dashboard import DashboardRoom
from app.services.dashboard import get_dashboard_rooms, get_dashboard_summary
from app.services.errors import InvalidTransitionError, NotFoundError, VersionConflictError


async def load_room(db: AsyncSession, room_id: int, property_id: int) -> Room:
    """The room, if it belongs to the property (another property's room is "not found")."""
    # populate_existing: compare versions against the database, not a cached copy.
    room = await db.scalar(
        select(Room)
        .where(Room.id == room_id, Room.property_id == property_id)
        .execution_options(populate_existing=True)
    )
    if room is None:
        raise NotFoundError(f"Room {room_id} not found")
    if not room.is_active:
        # Retired rooms are out of service: no check-ins, no housekeeping changes.
        raise InvalidTransitionError(f"Room {room.number} is retired.")
    return room


def ensure_version(room: Room, expected_version: int) -> None:
    """Optimistic lock, part 1: reject clients acting on a stale view of the room."""
    if room.version != expected_version:
        raise VersionConflictError(
            f"Room {room.number} was changed by someone else "
            f"(version {room.version}, you sent {expected_version}). Reload and try again."
        )


def touch(room: Room) -> None:
    """Force an UPDATE of the room row so its version is bumped.

    Occupancy is derived from reservations, so check-in changes no room column;
    without this a stale client could act on the room unnoticed.
    """
    room.updated_at = func.now()  # SQL expression, evaluated by the UPDATE


async def commit(db: AsyncSession, room: Room) -> None:
    """Optimistic lock, part 2: SQLAlchemy adds "AND version = <loaded>" to the
    room UPDATE; if another transaction changed it meanwhile, nothing matches
    and StaleDataError is raised.
    """
    number = room.number  # read now: rollback expires every loaded object
    try:
        await db.commit()
    except StaleDataError:
        await db.rollback()
        raise VersionConflictError(
            f"Room {number} was changed by someone else. Reload and try again."
        ) from None


async def publish_room_changes(
    db: AsyncSession, bus: EventBus, today: date, room_ids: Collection[int]
) -> dict[int, DashboardRoom]:
    """Publish room_status_changed for each room, then summary_changed (call after
    commit). Returns the room entries."""
    entries = {
        entry.room_id: entry for entry in (await get_dashboard_rooms(db, today, room_ids)).rooms
    }
    for entry in entries.values():
        await bus.publish(RoomStatusChanged(business_date=today, room=entry))
    # Any room change can move the counts, so always send the fresh summary.
    await bus.publish(SummaryChanged(summary=await get_dashboard_summary(db, today)))
    return entries
