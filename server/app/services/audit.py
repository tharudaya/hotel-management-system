from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditAction, AuditEvent, AuditEventType, AuditLog, User


@dataclass(frozen=True)
class ClientInfo:
    """Who is making the request, as far as the server can tell."""

    ip_address: str | None
    user_agent: str | None


def record_change(
    db: AsyncSession,
    *,
    action: AuditAction,
    target_type: str,
    target_id: int | str,
    actor: User | None,
    property_id: int | None,
    client: ClientInfo | None,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> AuditLog:
    """Add an audit_log row for a data change to the current transaction.

    Write it in the same transaction as the change (the caller commits), so the log
    and the data never disagree. `before`/`after` hold only what changed (plus any
    context worth keeping, such as a room number); never secrets or password hashes.
    """
    row = AuditLog(
        property_id=property_id,
        actor_user_id=actor.id if actor else None,
        action=action.value,
        target_type=target_type,
        target_id=str(target_id),
        before=before,
        after=after,
        ip=client.ip_address if client else None,
        user_agent=client.user_agent[:500] if client and client.user_agent else None,
    )
    db.add(row)
    return row


def record_event(
    db: AsyncSession,
    event_type: AuditEventType,
    client: ClientInfo,
    *,
    user_id: int | None = None,
    username: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    """Add an authentication event (audit_events) to the current transaction."""
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
