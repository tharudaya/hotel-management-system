"""Shared FastAPI dependencies: settings, client info, authentication and roles."""

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.core.clock import hotel_today
from app.core.config import Settings
from app.db.session import SessionDep
from app.models import User, UserRole
from app.realtime.bus import EventBus
from app.services import auth as auth_service
from app.services.audit import ClientInfo


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def get_event_bus(request: Request) -> EventBus:
    bus: EventBus = request.app.state.event_bus
    return bus


EventBusDep = Annotated[EventBus, Depends(get_event_bus)]


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


def require_role(*roles: UserRole) -> Callable[[User], Awaitable[User]]:
    """Dependency factory: 401 if not logged in, 403 if the user's role is not allowed.

    Usage:
        @router.get("/x", dependencies=[Depends(require_role(UserRole.MANAGER))])
        async def x(user: Annotated[User, Depends(require_role(UserRole.ADMIN))]) -> ...
    """
    allowed = frozenset(roles)

    async def check_role(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return check_role
