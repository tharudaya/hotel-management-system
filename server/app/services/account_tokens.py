"""Invite and password-reset links: issue, check, redeem; forgot-password requests.

Tokens are 256 bits from `secrets`, single use, and only their keyed hash (HMAC with
SESSION_SECRET, like session tokens) is stored. Invites last 72 hours, resets 1 hour.
Issuing a token for a user expires their other unused tokens, so only the newest
link works. Redeeming never logs the user in.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.email import EmailDeliveryError, EmailMessage, EmailSender
from app.core.passwords import PasswordPolicyError, check_new_password
from app.core.security import hash_password, hash_session_token, new_session_token
from app.models import AuditAction, AuditEvent, AuditEventType, TokenPurpose, User, UserToken
from app.realtime.bus import EventBus
from app.schemas.account import TokenInfo
from app.services.audit import ClientInfo, record_change, record_event
from app.services.auth import publish_sessions_ended, revoke_user_sessions
from app.services.errors import GoneError, InvalidInputError, NotFoundError
from app.services.permissions import get_default_property_id

logger: logging.Logger = logging.getLogger(__name__)

TOKEN_LIFETIME: dict[TokenPurpose, timedelta] = {
    TokenPurpose.INVITE: timedelta(hours=72),
    TokenPurpose.PASSWORD_RESET: timedelta(hours=1),
}
LINK_PATH: dict[TokenPurpose, str] = {
    TokenPurpose.INVITE: "activate",
    TokenPurpose.PASSWORD_RESET: "reset-password",
}

# Forgot-password throttling (silent: the response never changes).
FORGOT_WINDOW: timedelta = timedelta(hours=1)
FORGOT_MAX_PER_EMAIL: int = 3
FORGOT_MAX_PER_IP: int = 10

FORGOT_RESPONSE: str = (
    "If an account with that email address exists, we have sent a link to reset the password."
)


def _token_hash(settings: Settings, token: str) -> str:
    return hash_session_token(token, settings.session_secret.get_secret_value())


def link_for(settings: Settings, purpose: TokenPurpose, token: str) -> str:
    return f"{settings.app_base_url.rstrip('/')}/{LINK_PATH[purpose]}/{token}"


def mask_email(email: str) -> str:
    """ "alice@hotel.example" -> "a***@hotel.example" (for showing where mail went)."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


async def issue_token(
    db: AsyncSession,
    settings: Settings,
    *,
    user: User,
    purpose: TokenPurpose,
    created_by: User | None,
) -> tuple[str, datetime]:
    """Create a link token (in the current transaction) and expire the user's older
    unused tokens. Returns the plain token, which is never stored, and its expiry."""
    now = datetime.now(UTC)
    await db.execute(
        update(UserToken)
        .where(
            UserToken.user_id == user.id,
            UserToken.used_at.is_(None),
            UserToken.expires_at > now,
        )
        .values(expires_at=now)
    )
    token = new_session_token()
    expires_at = now + TOKEN_LIFETIME[purpose]
    db.add(
        UserToken(
            user_id=user.id,
            purpose=purpose.value,
            token_hash=_token_hash(settings, token),
            expires_at=expires_at,
            created_by=created_by.id if created_by else None,
        )
    )
    return token, expires_at


def compose_email(settings: Settings, purpose: TokenPurpose, user: User, link: str) -> EmailMessage:
    if user.email is None:
        raise ValueError("compose_email needs a user with an email address")
    if purpose is TokenPurpose.INVITE:
        return EmailMessage(
            to=user.email,
            subject=f"Your {settings.hotel_name} account",
            body=(
                f"Hello {user.full_name},\n\n"
                f"An account has been created for you at {settings.hotel_name}.\n"
                f"Your username is: {user.username}\n\n"
                "Choose your password here (the link works once and expires in 72 hours):\n"
                f"{link}\n\n"
                "If you were not expecting this, you can ignore this email."
            ),
        )
    return EmailMessage(
        to=user.email,
        subject=f"Reset your {settings.hotel_name} password",
        body=(
            f"Hello {user.full_name},\n\n"
            "Someone asked to reset the password for your account "
            f"({user.username}).\n\n"
            "Choose a new password here (the link works once and expires in 1 hour):\n"
            f"{link}\n\n"
            "If this was not you, ignore this email; your password stays the same."
        ),
    )


async def deliver(sender: EmailSender, message: EmailMessage) -> bool:
    """Send; on failure log it and report False (the caller decides what to do)."""
    try:
        await sender.send(message)
    except EmailDeliveryError:
        logger.exception("Could not send email to %s", mask_email(message.to))
        return False
    return True


async def _load(
    db: AsyncSession, settings: Settings, token: str, purpose: TokenPurpose, *, lock: bool
) -> tuple[UserToken, User]:
    query = (
        select(UserToken, User)
        .join(User, User.id == UserToken.user_id)
        .where(
            UserToken.token_hash == _token_hash(settings, token),
            UserToken.purpose == purpose.value,
        )
        .execution_options(populate_existing=True)
    )
    if lock:
        # Two concurrent redemptions: the second waits, then sees used_at set.
        query = query.with_for_update(of=UserToken)
    row = (await db.execute(query)).one_or_none()
    if row is None:
        raise NotFoundError("This link is not valid.", code="token_invalid")
    user_token, user = row
    now = datetime.now(UTC)
    if user_token.used_at is not None:
        raise GoneError("This link has already been used.", code="token_used")
    if user_token.expires_at <= now:
        raise GoneError("This link has expired. Ask for a new one.", code="token_expired")
    if user.deactivated_at is not None or (user.expires_at is not None and user.expires_at <= now):
        # The account was closed after the link was sent.
        raise NotFoundError("This link is not valid.", code="token_invalid")
    return user_token, user


async def token_info(
    db: AsyncSession, settings: Settings, *, token: str, purpose: TokenPurpose
) -> TokenInfo:
    _, user = await _load(db, settings, token, purpose, lock=False)
    return TokenInfo(username=user.username, full_name=user.full_name)


async def redeem(
    db: AsyncSession,
    settings: Settings,
    bus: EventBus,
    *,
    token: str,
    purpose: TokenPurpose,
    password: str,
    client: ClientInfo,
) -> None:
    """Set the password and use up the token. Does not log the user in."""
    user_token, user = await _load(db, settings, token, purpose, lock=True)
    try:
        check_new_password(password, username=user.username)
    except PasswordPolicyError as exc:
        # The token stays valid: the user can try another password.
        raise InvalidInputError(exc.message, code=exc.code) from None

    now = datetime.now(UTC)
    was_invited = user.password_hash is None
    user.password_hash = hash_password(password)
    user_token.used_at = now
    await db.execute(
        update(UserToken)
        .where(
            UserToken.user_id == user.id,
            UserToken.used_at.is_(None),
            UserToken.expires_at > now,
        )
        .values(expires_at=now)
    )
    # A reset means the old password may be known: end every existing session.
    revoked = (
        await revoke_user_sessions(db, user.id, now)
        if purpose is TokenPurpose.PASSWORD_RESET
        else []
    )
    record_change(
        db,
        action=(
            AuditAction.USER_ACTIVATED
            if purpose is TokenPurpose.INVITE
            else AuditAction.USER_PASSWORD_RESET
        ),
        target_type="user",
        target_id=user.id,
        actor=user,
        property_id=await get_default_property_id(db),
        client=client,
        before={"status": "invited" if was_invited else "active"},
        after={"status": "active", "sessions_revoked": len(revoked)},
    )
    await db.commit()
    await publish_sessions_ended(bus, user.id, revoked)


@dataclass(frozen=True)
class PendingEmail:
    message: EmailMessage


async def request_password_reset(
    db: AsyncSession, settings: Settings, *, email: str, client: ClientInfo
) -> PendingEmail | None:
    """Forgot-password. Returns the email to send (by the caller, in the background),
    or None. Callers must answer the same way either way: the result reveals whether
    an account exists. Every request is logged in audit_events with its outcome.
    """
    email = email.strip().lower()
    since = func.now() - FORGOT_WINDOW
    requested = AuditEvent.event_type == AuditEventType.PASSWORD_RESET_REQUESTED.value
    by_email = await db.scalar(
        select(func.count()).where(
            requested, AuditEvent.occurred_at > since, AuditEvent.details["email"].astext == email
        )
    )
    by_ip = await db.scalar(
        select(func.count()).where(
            requested, AuditEvent.occurred_at > since, AuditEvent.ip_address == client.ip_address
        )
    )
    user = await db.scalar(select(User).where(User.email == email))
    now = datetime.now(UTC)

    pending: PendingEmail | None = None
    if (by_email or 0) >= FORGOT_MAX_PER_EMAIL or (by_ip or 0) >= FORGOT_MAX_PER_IP:
        outcome = "throttled"
    elif user is None:
        outcome = "unknown_email"
    elif (
        user.password_hash is None
        or user.deactivated_at is not None
        or (user.expires_at is not None and user.expires_at <= now)
    ):
        outcome = "not_eligible"  # invited, deactivated or expired: no reset by email
    else:
        token, _ = await issue_token(
            db, settings, user=user, purpose=TokenPurpose.PASSWORD_RESET, created_by=None
        )
        link = link_for(settings, TokenPurpose.PASSWORD_RESET, token)
        pending = PendingEmail(compose_email(settings, TokenPurpose.PASSWORD_RESET, user, link))
        outcome = "sent"

    record_event(
        db,
        AuditEventType.PASSWORD_RESET_REQUESTED,
        client,
        user_id=user.id if user else None,
        details={"email": email, "outcome": outcome},
    )
    await db.commit()
    return pending
