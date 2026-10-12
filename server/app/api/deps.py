"""Shared FastAPI dependencies: settings, client info, authentication and permissions."""

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.core.clock import hotel_today
from app.core.config import Settings
from app.core.email import EmailSender
from app.core.permissions import ALL_PERMISSION_KEYS
from app.db.session import SessionDep
from app.models import User
from app.realtime.bus import EventBus
from app.services import auth as auth_service
from app.services import permissions as permission_service
from app.services.audit import ClientInfo


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def get_event_bus(request: Request) -> EventBus:
    bus: EventBus = request.app.state.event_bus
    return bus


EventBusDep = Annotated[EventBus, Depends(get_event_bus)]


def get_email_sender(request: Request) -> EmailSender:
    sender: EmailSender = request.app.state.email_sender
    return sender


EmailSenderDep = Annotated[EmailSender, Depends(get_email_sender)]


def get_client_info(request: Request) -> ClientInfo:
    # Direct peer address. Behind a reverse proxy, configure uvicorn's
    # --forwarded-allow-ips so this reflects the real client.
    return ClientInfo(
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


ClientInfoDep = Annotated[ClientInfo, Depends(get_client_info)]


def get_hotel_today(settings: SettingsDep) -> date:
    """Today's date in the hotel's time zone (overridden in tests)."""
    return hotel_today(settings.hotel_tz)


HotelTodayDep = Annotated[date, Depends(get_hotel_today)]


def get_session_token(request: Request, settings: SettingsDep) -> str | None:
    return request.cookies.get(settings.session_cookie_name) or None


SessionTokenDep = Annotated[str | None, Depends(get_session_token)]


async def get_current_user(db: SessionDep, settings: SettingsDep, token: SessionTokenDep) -> User:
    """The logged-in user, or 401. Inactive users and revoked/expired sessions are rejected."""
    user = await auth_service.get_user_for_session(db, settings, token) if token else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_property_id(db: SessionDep) -> int:
    """The property this request acts on (the only one, until rooms are property-scoped)."""
    return await permission_service.get_default_property_id(db)


CurrentPropertyId = Annotated[int, Depends(get_current_property_id)]


async def get_current_permissions(
    request: Request, db: SessionDep, user: CurrentUser, property_id: CurrentPropertyId
) -> frozenset[str]:
    """The current user's effective permissions at the current property.

    Loaded once per request and cached on request.state.permissions, so several
    require_permission checks (and any code reading the state) share one lookup.
    """
    cached: frozenset[str] | None = getattr(request.state, "permissions", None)
    if cached is None:
        cached = await permission_service.get_effective_permissions(db, user.id, property_id)
        request.state.permissions = cached
    return cached


CurrentPermissions = Annotated[frozenset[str], Depends(get_current_permissions)]


def require_permission(*keys: str) -> Callable[[User, frozenset[str]], Awaitable[User]]:
    """Dependency factory: 401 if not logged in, 403 unless the user has every key.

    Check permission keys (app/core/permissions.py `P`), never role names.

    Usage:
        @router.get("/x", dependencies=[Depends(require_permission(P.DASHBOARD_VIEW))])
        async def x(user: Annotated[User, Depends(require_permission(P.USER_MANAGE))]) -> ...
    """
    unknown = set(keys) - ALL_PERMISSION_KEYS
    if unknown:
        raise ValueError(f"Unknown permission keys: {sorted(unknown)}")
    required = frozenset(keys)

    async def check_permissions(user: CurrentUser, permissions: CurrentPermissions) -> User:
        if not required <= permissions:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return check_permissions
