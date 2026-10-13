"""Audit log of data changes: who changed what, from what, to what."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditAction(StrEnum):
    """What changed. Stored as plain strings, so new actions need no migration."""

    RESERVATION_CHECKED_IN = "reservation.checked_in"
    RESERVATION_CHECKED_OUT = "reservation.checked_out"
    ROOM_HOUSEKEEPING_STATUS_CHANGED = "room.housekeeping_status_changed"
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_ROLE_CHANGED = "user.role_changed"
    USER_DEACTIVATED = "user.deactivated"
    USER_REACTIVATED = "user.reactivated"
    USER_SESSIONS_REVOKED = "user.sessions_revoked"
    USER_INVITE_SENT = "user.invite_sent"
    USER_PASSWORD_RESET_SENT = "user.password_reset_sent"
    USER_ACTIVATED = "user.activated"
    USER_PASSWORD_RESET = "user.password_reset"
    USER_EXPIRED = "user.expired"  # recorded by the expiry sweep (actor: the system)
    ROLE_CREATED = "role.created"
    ROLE_UPDATED = "role.updated"
    ROLE_DELETED = "role.deleted"


class AuditLog(Base):
    """One row per change, written in the same transaction as the change. Append-only:
    a database trigger rejects UPDATE and DELETE (migration ff3cb811073d).

    Authentication events (logins, logouts, throttling) live in audit_events instead.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_target", "target_type", "target_id", "created_at"),
        Index("ix_audit_log_property_id_created_at", "property_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    property_id: Mapped[int | None] = mapped_column(
        ForeignKey("properties.id", ondelete="SET NULL")
    )
    # Null for changes made by the system (migrations, seed, background jobs).
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(100))
    target_type: Mapped[str] = mapped_column(String(50))
    # String so any kind of target id fits (numeric ids are stored as text).
    target_id: Mapped[str] = mapped_column(String(64))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    # clock_timestamp(), not now(): the moment of the insert, so changes in one
    # transaction (and audit_events rows) keep their real order.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), index=True
    )
