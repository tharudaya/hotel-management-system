from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Identity, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TokenPurpose(StrEnum):
    INVITE = "invite"
    PASSWORD_RESET = "password_reset"


class UserToken(Base):
    """A single-use link token (invite or password reset).

    Only a keyed hash of the token is stored; the token itself exists only in the
    link sent to the user. Usable while used_at is NULL and expires_at is in the
    future. Issuing a new token for a user expires their older unused ones.
    """

    __tablename__ = "user_tokens"
    __table_args__ = (
        CheckConstraint("purpose IN ('invite', 'password_reset')", name="purpose_known"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Null when issued by the user themself (forgot password).
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
