"""Idempotency keys: a retried create returns the first result instead of a duplicate."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class IdempotencyKey(Base):
    """A client-generated key, used once per user.

    Written in the same transaction as the record it created. A repeat with the same
    key and request hash returns `response_reference`; the same key with a different
    request is refused. Rows older than 24 hours may be deleted.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("key", "user_id", name="uq_idempotency_keys_key_user_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    key: Mapped[str] = mapped_column(String(100))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # e.g. "POST /api/reservations"
    endpoint: Mapped[str] = mapped_column(String(100))
    # SHA-256 hex of the canonical request body.
    request_hash: Mapped[str] = mapped_column(String(64))
    # What the first request created, e.g. "reservation:42".
    response_reference: Mapped[str] = mapped_column(String(100))
    # Cleanup deletes by age.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    def __repr__(self) -> str:
        return f"<IdempotencyKey {self.endpoint} user={self.user_id}>"
