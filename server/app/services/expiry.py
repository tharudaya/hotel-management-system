"""The expired-users sweep: users whose access end date has passed.

Expired users already cannot act: every request and WebSocket re-check checks
`expires_at` (services/permissions.user_can_act). The sweep makes that visible and
tidy: it revokes their remaining sessions, closes their WebSockets at once (rather
than at the next heartbeat), and records one `user.expired` audit entry per expiry.
That entry is also the marker: a user is processed again only if their expiry date
is changed and passes again.

Runs every EXPIRY_SWEEP_INTERVAL_SECONDS inside the API process (app/main.py), or
once from cron with `python -m app.scripts.expire_users`. A transaction-level
Postgres advisory lock lets only one of several instances sweep at a time.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import String, cast, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditAction, AuditLog, User
from app.realtime.bus import EventBus
from app.realtime.events import SessionEnded
from app.services.audit import record_change
from app.services.auth import revoke_user_sessions
from app.services.permissions import get_default_property_id, publish_permissions_changed

# Arbitrary, fixed id for pg_try_advisory_xact_lock (unique within this app).
SWEEP_LOCK_ID: int = 7_204_119_001


@dataclass(frozen=True)
class SweepResult:
    ran: bool  # False if another instance holds the lock
    expired_user_ids: list[int]
    sessions_revoked: int


async def sweep_expired_users(
    db: AsyncSession, bus: EventBus, *, now: datetime | None = None
) -> SweepResult:
    now = now or datetime.now(UTC)
    if not await db.scalar(select(func.pg_try_advisory_xact_lock(SWEEP_LOCK_ID))):
        await db.commit()  # ends the (read-only) transaction
        return SweepResult(ran=False, expired_user_ids=[], sessions_revoked=0)

    already_recorded = exists().where(
        AuditLog.target_type == "user",
        AuditLog.target_id == cast(User.id, String),
        AuditLog.action == AuditAction.USER_EXPIRED.value,
        AuditLog.created_at >= User.expires_at,
    )
    users = list(
        (
            await db.scalars(
                select(User)
                .where(
                    User.expires_at.is_not(None),
                    User.expires_at <= now,
                    # Deactivation already ended their sessions and is its own state.
                    User.deactivated_at.is_(None),
                    ~already_recorded,
                )
                .order_by(User.id)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    if not users:
        await db.commit()  # releases the advisory lock
        return SweepResult(ran=True, expired_user_ids=[], sessions_revoked=0)

    property_id = await get_default_property_id(db)
    ended: list[tuple[int, list[str]]] = []
    for user in users:
        revoked = await revoke_user_sessions(db, user.id, now)
        ended.append((user.id, revoked))
        record_change(
            db,
            action=AuditAction.USER_EXPIRED,
            target_type="user",
            target_id=user.id,
            actor=None,  # the system
            property_id=property_id,
            client=None,
            before=None,
            after={
                "expires_at": user.expires_at.isoformat() if user.expires_at else None,
                "sessions_revoked": len(revoked),
            },
        )
    await db.commit()

    for user_id, token_hashes in ended:
        for token_hash in token_hashes:
            await bus.publish(
                SessionEnded(
                    session_token_hash=token_hash, user_id=user_id, reason="Account expired"
                )
            )
    await publish_permissions_changed(bus, property_id, [user_id for user_id, _ in ended])
    return SweepResult(
        ran=True,
        expired_user_ids=[user_id for user_id, _ in ended],
        sessions_revoked=sum(len(hashes) for _, hashes in ended),
    )
