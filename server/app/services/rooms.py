"""Housekeeping status changes."""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import P
from app.models import AuditEventType, HousekeepingStatus, User
from app.realtime.bus import EventBus
from app.schemas.dashboard import DashboardRoom
from app.services.audit import ClientInfo, record_event
from app.services.errors import InvalidTransitionError, PermissionDeniedError
from app.services.room_changes import commit, ensure_version, load_room, publish_room_changes

HK = HousekeepingStatus

# Names staff see (must match HOUSEKEEPING labels in client/src/dashboard/status.ts).
# "clean" is shown as "Inspection" (cleaned, awaiting inspection) and "inspected" as
# "Ready"; both can be checked in.
HOUSEKEEPING_LABELS: dict[HousekeepingStatus, str] = {
    HK.DIRTY: "Dirty",
    HK.IN_PROGRESS: "Cleaning",
    HK.CLEAN: "Inspection",
    HK.INSPECTED: "Ready",
    HK.OUT_OF_ORDER: "Out of order",
}

# The same states inside a sentence ("Room 103 is ...").
HOUSEKEEPING_PHRASES: dict[HousekeepingStatus, str] = {
    HK.DIRTY: "dirty",
    HK.IN_PROGRESS: "being cleaned",
    HK.CLEAN: "awaiting inspection",
    HK.INSPECTED: "ready",
    HK.OUT_OF_ORDER: "out of order",
}

# Every allowed change of housekeeping status. Anything else is rejected.
#   dirty        -> in_progress (cleaning started), clean (cleaned in one go),
#                   out_of_order
#   in_progress  -> clean (done), dirty (abandoned), out_of_order
#   clean        -> inspected (supervisor approved), dirty (re-soiled, failed
#                   inspection), out_of_order
#   inspected    -> dirty, out_of_order
#   out_of_order -> dirty (back in service; must be cleaned before selling)
HOUSEKEEPING_TRANSITIONS: dict[HousekeepingStatus, frozenset[HousekeepingStatus]] = {
    HK.DIRTY: frozenset({HK.IN_PROGRESS, HK.CLEAN, HK.OUT_OF_ORDER}),
    HK.IN_PROGRESS: frozenset({HK.CLEAN, HK.DIRTY, HK.OUT_OF_ORDER}),
    HK.CLEAN: frozenset({HK.INSPECTED, HK.DIRTY, HK.OUT_OF_ORDER}),
    HK.INSPECTED: frozenset({HK.DIRTY, HK.OUT_OF_ORDER}),
    HK.OUT_OF_ORDER: frozenset({HK.DIRTY}),
}


def validate_housekeeping_transition(
    current: HousekeepingStatus, target: HousekeepingStatus
) -> None:
    allowed = HOUSEKEEPING_TRANSITIONS[current]
    if target not in allowed:
        # In the order the statuses are defined (Dirty, Cleaning, Inspection, ...).
        options = ", ".join(HOUSEKEEPING_LABELS[s] for s in HK if s in allowed)
        current_label = HOUSEKEEPING_LABELS[current]
        raise InvalidTransitionError(
            f"Cannot change housekeeping status from {current_label} to "
            f"{HOUSEKEEPING_LABELS[target]}. Allowed from {current_label}: {options}."
        )


async def change_housekeeping_status(
    db: AsyncSession,
    bus: EventBus,
    *,
    room_id: int,
    status: HousekeepingStatus,
    expected_version: int,
    actor: User,
    actor_permissions: frozenset[str],
    client: ClientInfo,
    today: date,
) -> DashboardRoom:
    room = await load_room(db, room_id)
    before = room.housekeeping_status
    # Taking a room out of service, or returning it, is a separate permission.
    if HK.OUT_OF_ORDER in (before, status) and P.ROOM_SET_OUT_OF_ORDER not in actor_permissions:
        raise PermissionDeniedError(
            "You don't have permission to take rooms out of order or back into service."
        )
    ensure_version(room, expected_version)
    validate_housekeeping_transition(before, status)

    room.housekeeping_status = status
    record_event(
        db,
        AuditEventType.ROOM_HOUSEKEEPING_STATUS_CHANGED,
        client,
        user_id=actor.id,
        username=actor.username,
        details={
            "room_id": room.id,
            "room_number": room.number,
            "before": {"housekeeping_status": before.value, "version": expected_version},
            "after": {"housekeeping_status": status.value, "version": expected_version + 1},
        },
    )
    await commit(db, room)

    entries = await publish_room_changes(db, bus, today, [room.id])
    return entries[room.id]
