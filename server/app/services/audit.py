from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent, AuditEventType


@dataclass(frozen=True)
class ClientInfo:
    """Who is making the request, as far as the server can tell."""

    ip_address: str | None
    user_agent: str | None


def record_event(
    db: AsyncSession,
    event_type: AuditEventType,
    client: ClientInfo,
    *,
    user_id: int | None = None,
    username: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    """Add an audit row to the current transaction (the caller commits)."""
    event = AuditEvent(
        event_type=event_type.value,
        user_id=user_id,
        username=username[:50] if username else None,
        ip_address=client.ip_address,
        user_agent=client.user_agent[:500] if client.user_agent else None,
        details=details,
    )
    db.add(event)
    return event
