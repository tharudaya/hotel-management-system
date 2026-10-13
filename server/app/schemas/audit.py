"""Read models for /api/admin/audit: data changes (audit_log) and authentication
events (audit_events) as one newest-first stream."""

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import AuditAction, AuditEventType

# What kind of record an entry is about. Authentication events are about the user who
# signed in (or tried to).
AuditTargetType = Literal["user", "role", "reservation", "room", "room_type"]


class AuditQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: Annotated[str | None, Field(min_length=1, max_length=50)] = Field(
        default=None, description="Username of who did it (for logins: who signed in)"
    )
    action: AuditAction | AuditEventType | None = None
    target_type: AuditTargetType | None = None
    target_id: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    # Calendar days in the property's time zone, both inclusive.
    date_from: date | None = None
    date_to: date | None = None
    cursor: Annotated[str | None, Field(max_length=200)] = Field(
        default=None, description="next_cursor from the previous page"
    )
    limit: Annotated[int, Field(ge=1, le=100)] = 50

    @model_validator(mode="after")
    def _dates_in_order(self) -> AuditQuery:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


class AuditActor(BaseModel):
    # None for a failed login with a username that matches no user.
    id: int | None
    username: str
    full_name: str | None


class AuditEntry(BaseModel):
    # Unique across both sources, e.g. "change-12" or "auth-5".
    id: str
    source: Literal["change", "auth"]
    occurred_at: datetime
    action: str
    # None for changes made by the system (migrations, seed).
    actor: AuditActor | None
    target_type: AuditTargetType | None
    target_id: str | None
    # Readable name of the target: a user's name, a role's name (also after it was
    # deleted, from the entry itself), a room number. None if unknown.
    target_label: str | None
    # Data changes: only the fields that changed. Never secrets.
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    # Authentication events: e.g. {"reason": "bad_password"}.
    details: dict[str, Any] | None
    ip: str | None


class AuditPage(BaseModel):
    """Newest first. Pass next_cursor back as `cursor` for older entries; None = end."""

    items: list[AuditEntry]
    next_cursor: str | None
