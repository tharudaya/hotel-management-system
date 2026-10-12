"""Domain events published on the event bus after a change commits."""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel

from app.schemas.dashboard import DashboardRoom, DashboardSummary


class RoomStatusChanged(BaseModel):
    """A room's dashboard entry changed; `room` is its new state. Broadcast."""

    type: Literal["room_status_changed"] = "room_status_changed"
    business_date: date
    room: DashboardRoom


class SummaryChanged(BaseModel):
    """The dashboard summary changed; `summary` is its new state. Broadcast."""

    type: Literal["summary_changed"] = "summary_changed"
    summary: DashboardSummary


class SessionEnded(BaseModel):
    """A login session was revoked (logout, or replaced by a new login).

    Internal only, never sent to clients: the realtime hub uses it to close
    sockets opened with that session.
    """

    type: Literal["session_ended"] = "session_ended"
    session_token_hash: str
    user_id: int


class PermissionsChanged(BaseModel):
    """Someone's effective permissions at a property may have changed: a role was
    assigned or removed, a role's permissions were edited, or a user was deactivated.

    Internal only: the hub immediately re-checks the affected sockets and closes those
    that may no longer receive dashboard events. `user_ids` None means everyone at the
    property (e.g. a role's permission set changed).
    """

    type: Literal["permissions_changed"] = "permissions_changed"
    property_id: int
    user_ids: list[int] | None = None


type DomainEvent = RoomStatusChanged | SummaryChanged | SessionEnded | PermissionsChanged

# Events that connected dashboards receive.
type BroadcastEvent = RoomStatusChanged | SummaryChanged


def broadcast_payload(event: BroadcastEvent) -> dict[str, Any]:
    """The event's data as sent to clients (everything except its type)."""
    return event.model_dump(mode="json", exclude={"type"})
