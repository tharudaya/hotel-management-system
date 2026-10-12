"""Login, logout and session lookup."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import (
    burn_password_check,
    hash_session_token,
    new_session_token,
    verify_and_update_password,
)
from app.models import AuditEvent, AuditEventType, User, UserSession
from app.realtime.bus import EventBus
from app.realtime.events import SessionEnded
from app.services.audit import ClientInfo, record_event
from app.services.permissions import user_can_act


class InvalidCredentialsError(Exception):
    """Unknown user, wrong password or inactive account; deliberately indistinguishable."""


class TooManyAttemptsError(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("too many failed login attempts")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class LoginResult:
    user: User
    token: str
    expires_at: datetime


def normalize_username(username: str) -> str:
    return username.strip().lower()


def _token_hash(settings: Settings, token: str) -> str:
    return hash_session_token(token, settings.session_secret.get_secret_value())


async def _recent_failures(
    db: AsyncSession, settings: Settings, *, username: str | None, ip_address: str | None
) -> int:
    since = func.now() - timedelta(seconds=settings.login_failure_window_seconds)
    query = select(func.count()).where(
        AuditEvent.event_type == AuditEventType.LOGIN_FAILED.value,
        AuditEvent.occurred_at > since,
    )
    if username is not None:
        query = query.where(AuditEvent.username == username)
    if ip_address is not None:
        query = query.where(AuditEvent.ip_address == ip_address)
    return await db.scalar(query) or 0


async def _is_throttled(
    db: AsyncSession, settings: Settings, username: str, client: ClientInfo
) -> str | None:
    """Return why the attempt is blocked, or None if it may proceed."""
    by_username = await _recent_failures(db, settings, username=username, ip_address=None)
    if by_username >= settings.login_max_failures_per_username:
        return "username_throttled"
    if client.ip_address is not None:
        by_ip = await _recent_failures(db, settings, username=None, ip_address=client.ip_address)
        if by_ip >= settings.login_max_failures_per_ip:
            return "ip_throttled"
    return None


async def _fail(
    db: AsyncSession, client: ClientInfo, username: str, reason: str, user_id: int | None = None
) -> InvalidCredentialsError:
    record_event(
        db,
        AuditEventType.LOGIN_FAILED,
        client,
        user_id=user_id,
        username=username,
        details={"reason": reason},
    )
    await db.commit()
    return InvalidCredentialsError()


async def login(
    db: AsyncSession,
    settings: Settings,
    bus: EventBus,
    *,
    username: str,
    password: str,
    client: ClientInfo,
    previous_token: str | None = None,
) -> LoginResult:
    """Check credentials and open a new session.

    Raises TooManyAttemptsError or InvalidCredentialsError. Every outcome is
    written to the audit log.
    """
    username = normalize_username(username)

    blocked_reason = await _is_throttled(db, settings, username, client)
    if blocked_reason is not None:
        record_event(
            db,
            AuditEventType.LOGIN_BLOCKED,
            client,
            username=username,
            details={"reason": blocked_reason},
        )
        await db.commit()
        raise TooManyAttemptsError(settings.login_failure_window_seconds)

    user = await db.scalar(select(User).where(User.username == username))
    if user is None:
        burn_password_check(password)
        raise await _fail(db, client, username, "unknown_user")

    valid, updated_hash = verify_and_update_password(password, user.password_hash)
    if not valid:
        raise await _fail(db, client, username, "bad_password", user.id)
    now = datetime.now(UTC)
    # Same generic error as a wrong password; the audit row records the real reason.
    if user.deactivated_at is not None:
        raise await _fail(db, client, username, "deactivated", user.id)
    if user.expires_at is not None and user.expires_at <= now:
        raise await _fail(db, client, username, "expired", user.id)

    if updated_hash is not None:
        user.password_hash = updated_hash
    user.last_login_at = now

    # Never reuse a session across logins (prevents session fixation).
    replaced_user_id = await _revoke(db, settings, previous_token, now) if previous_token else None
    # Housekeeping: drop this user's sessions that can no longer be used.
    await db.execute(
        delete(UserSession).where(UserSession.user_id == user.id, UserSession.expires_at < now)
    )

    token = new_session_token()
    expires_at = now + timedelta(seconds=settings.session_max_age_seconds)
    db.add(
        UserSession(
            token_hash=_token_hash(settings, token),
            user_id=user.id,
            expires_at=expires_at,
            ip_address=client.ip_address,
            user_agent=client.user_agent[:500] if client.user_agent else None,
        )
    )
    record_event(db, AuditEventType.LOGIN_SUCCEEDED, client, user_id=user.id, username=username)
    await db.commit()
    if previous_token and replaced_user_id is not None:
        await bus.publish(
            SessionEnded(
                session_token_hash=_token_hash(settings, previous_token),
                user_id=replaced_user_id,
            )
        )
    return LoginResult(user=user, token=token, expires_at=expires_at)


async def _revoke(db: AsyncSession, settings: Settings, token: str, now: datetime) -> int | None:
    """Revoke the session for this token; return its user id if it was active."""
    return await db.scalar(
        update(UserSession)
        .where(
            UserSession.token_hash == _token_hash(settings, token),
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
        .returning(UserSession.user_id)
    )


async def logout(
    db: AsyncSession, settings: Settings, bus: EventBus, *, token: str, client: ClientInfo
) -> None:
    user_id = await _revoke(db, settings, token, datetime.now(UTC))
    if user_id is not None:
        record_event(db, AuditEventType.LOGOUT, client, user_id=user_id)
    await db.commit()
    if user_id is not None:
        # Lets the realtime hub close this session's sockets right away.
        await bus.publish(
            SessionEnded(session_token_hash=_token_hash(settings, token), user_id=user_id)
        )


@dataclass(frozen=True)
class ActiveSession:
    user: User
    token_hash: str
    expires_at: datetime


def _active_session_filter(token_hash: str) -> tuple[ColumnElement[bool], ...]:
    return (
        UserSession.token_hash == token_hash,
        UserSession.revoked_at.is_(None),
        UserSession.expires_at > func.now(),
        # Deactivating or expiring a user ends their sessions too.
        user_can_act(func.now()),
    )


async def get_active_session(
    db: AsyncSession, settings: Settings, token: str
) -> ActiveSession | None:
    """The unexpired, unrevoked session for this token, if its user may still act."""
    token_hash = _token_hash(settings, token)
    row = (
        await db.execute(
            select(User, UserSession.expires_at)
            .join(UserSession, UserSession.user_id == User.id)
            .where(*_active_session_filter(token_hash))
        )
    ).one_or_none()
    if row is None:
        return None
    user, expires_at = row
    # A temporary user's access ends at their own expiry if that comes first, so a
    # long-lived WebSocket is closed on time.
    if user.expires_at is not None:
        expires_at = min(expires_at, user.expires_at)
    return ActiveSession(user=user, token_hash=token_hash, expires_at=expires_at)


async def get_user_for_session(db: AsyncSession, settings: Settings, token: str) -> User | None:
    """The active user owning this unexpired, unrevoked session, or None."""
    session = await get_active_session(db, settings, token)
    return session.user if session else None


async def is_session_active(db: AsyncSession, token_hash: str) -> bool:
    """Re-check a session by its stored hash (for long-lived WebSocket connections)."""
    found = await db.scalar(
        select(UserSession.id)
        .join(User, User.id == UserSession.user_id)
        .where(*_active_session_filter(token_hash))
    )
    return found is not None
