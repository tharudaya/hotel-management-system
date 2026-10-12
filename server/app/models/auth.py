from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.user import User


class UserSession(Base):
    """A server-side login session.

    The cookie carries a random token; only its keyed hash is stored, so a
    database leak does not expose usable sessions. A session is valid while
    revoked_at is NULL and expires_at is in the future.
    """

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(500))

    user: Mapped[User] = relationship(lazy="raise")


class AuditEventType(StrEnum):
    # Stored as plain strings (not a PG enum) so new event types need no migration.
    LOGIN_SUCCEEDED = "auth.login_succeeded"
    LOGIN_FAILED = "auth.login_failed"
    LOGIN_BLOCKED = "auth.login_blocked"
    LOGOUT = "auth.logout"
    PASSWORD_RESET_REQUESTED = "auth.password_reset_requested"


class AuditEvent(Base):
    """Append-only authentication log (logins, logouts, throttling). A database
    trigger rejects UPDATE and DELETE (migration ff3cb811073d).

    Data changes (check-ins, housekeeping, user administration) go to audit_log.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        # Login rate limiting counts recent failures per username and per IP.
        Index("ix_audit_events_type_username_occurred_at", "event_type", "username", "occurred_at"),
        Index("ix_audit_events_type_ip_occurred_at", "event_type", "ip_address", "occurred_at"),
        # Newest-first reading in the audit viewer.
        Index("ix_audit_events_occurred_at", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # clock_timestamp(), not now(): the moment of the insert, so events in one
    # transaction keep their order.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    event_type: Mapped[str] = mapped_column(String(50))
    # Kept when the user is deleted, so the trail survives.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # The username as submitted (normalized), even if no such user exists.
    username: Mapped[str | None] = mapped_column(String(50))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
