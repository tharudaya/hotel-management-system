"""Idempotency keys for creates (see IdempotencyKey).

A client sends a random `Idempotency-Key` header with a create and reuses it when it
retries (timeout, lost response). The first request stores the key, a hash of its body
and what it created, in the same transaction as the record. A retry with the same key
and body gets the original record back; the same key with another body is refused
(422 idempotency_key_reused), so a key can never create two different things.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IdempotencyKey
from app.services.errors import InvalidInputError

# Kept at least this long; older rows may be deleted (`delete_expired`).
RETENTION: timedelta = timedelta(hours=24)


def request_hash(body: BaseModel) -> str:
    """SHA-256 of the validated body in canonical JSON (key order and whitespace do not
    matter, nor do defaults the client left out)."""
    canonical = json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def find(db: AsyncSession, key: str, user_id: int) -> IdempotencyKey | None:
    return await db.scalar(
        select(IdempotencyKey).where(IdempotencyKey.key == key, IdempotencyKey.user_id == user_id)
    )


def replayed_reference(record: IdempotencyKey, *, endpoint: str, body_hash: str) -> str:
    """The reference the first request stored, if this is a true repeat of it."""
    if record.endpoint != endpoint or record.request_hash != body_hash:
        raise InvalidInputError(
            "This Idempotency-Key was already used for a different request. Use a new key "
            "for a new request.",
            code="idempotency_key_reused",
        )
    return record.response_reference


def new_record(
    *, key: str, user_id: int, endpoint: str, body_hash: str, reference: str
) -> IdempotencyKey:
    return IdempotencyKey(
        key=key,
        user_id=user_id,
        endpoint=endpoint,
        request_hash=body_hash,
        response_reference=reference,
    )


async def delete_expired(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Delete keys older than RETENTION and commit; returns how many."""
    cutoff = (now or datetime.now(UTC)) - RETENTION
    result = cast(
        CursorResult[Any],
        await db.execute(delete(IdempotencyKey).where(IdempotencyKey.created_at < cutoff)),
    )
    await db.commit()
    return result.rowcount
