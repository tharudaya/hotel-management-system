from fastapi import APIRouter, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.permissions import P
from app.realtime.hub import (
    CLOSE_FORBIDDEN,
    CLOSE_UNAUTHENTICATED,
    Access,
    Connection,
    ConnectionManager,
)
from app.services import auth as auth_service
from app.services import permissions as permission_service

router: APIRouter = APIRouter(tags=["realtime"])


@router.websocket("/ws")
async def realtime(websocket: WebSocket) -> None:
    """Server-to-client dashboard updates. See app/realtime/hub.py for the protocol."""
    settings: Settings = websocket.app.state.settings
    hub: ConnectionManager = websocket.app.state.hub

    # Browsers always send Origin; refuse other sites (cross-site WebSocket
    # hijacking) before upgrading. Non-browser clients may omit it.
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in settings.cors_origins:
        await websocket.close(CLOSE_FORBIDDEN, "Origin not allowed")
        return

    # Not the request-scoped SessionDep: the socket may stay open for hours and
    # must not hold a database connection. Each check opens a short session.
    session_factory: async_sessionmaker[AsyncSession] = websocket.app.state.session_factory
    token = websocket.cookies.get(settings.session_cookie_name)
    session = None
    user_view = None
    property_id = 0
    if token:
        async with session_factory() as db:
            session = await auth_service.get_active_session(db, settings, token)
            if session is not None:
                property_id = await permission_service.get_default_property_id(db)
                user_view = await permission_service.describe_user(db, session.user, property_id)

    # Accept before rejecting so the browser sees the close code and can send
    # the user to the login page instead of reconnecting in a loop.
    await websocket.accept()
    if session is None or user_view is None:
        await websocket.close(CLOSE_UNAUTHENTICATED, "Not authenticated")
        return
    # Same audience as the dashboard REST endpoints: events carry guest names.
    if P.DASHBOARD_VIEW not in user_view.permissions:
        await websocket.close(CLOSE_FORBIDDEN, "Insufficient permissions")
        return

    token_hash = session.token_hash
    user_id = session.user.id

    async def check_access() -> Access:
        # Session still valid (not revoked, expired or deactivated) and still allowed.
        async with session_factory() as db:
            if not await auth_service.is_session_active(db, token_hash):
                return Access.SESSION_ENDED
            permissions = await permission_service.get_effective_permissions(
                db, user_id, property_id
            )
        return Access.OK if P.DASHBOARD_VIEW in permissions else Access.FORBIDDEN

    connection = Connection(
        websocket,
        user_id=user_id,
        property_id=property_id,
        session_token_hash=token_hash,
        session_expires_at=session.expires_at,
        check_access=check_access,
        heartbeat_seconds=settings.ws_heartbeat_seconds,
        max_pending=settings.ws_max_pending_messages,
        current_seq=lambda: hub.seq,
    )
    hub.add(connection)
    try:
        await connection.run(
            {
                "user": user_view.model_dump(mode="json"),
                "heartbeat_seconds": settings.ws_heartbeat_seconds,
            }
        )
    finally:
        hub.remove(connection)
