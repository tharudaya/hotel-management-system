from fastapi import APIRouter, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import UserRole
from app.realtime.hub import (
    CLOSE_FORBIDDEN,
    CLOSE_UNAUTHENTICATED,
    Connection,
    ConnectionManager,
)
from app.schemas.auth import UserOut
from app.services import auth as auth_service

router: APIRouter = APIRouter(tags=["realtime"])

# Same audience as the dashboard REST endpoints: events carry guest names.
DASHBOARD_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.FRONT_DESK, UserRole.MANAGER, UserRole.ADMIN}
)


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
    if token:
        async with session_factory() as db:
            session = await auth_service.get_active_session(db, settings, token)

    # Accept before rejecting so the browser sees the close code and can send
    # the user to the login page instead of reconnecting in a loop.
    await websocket.accept()
    if session is None:
        await websocket.close(CLOSE_UNAUTHENTICATED, "Not authenticated")
        return
    if session.user.role not in DASHBOARD_ROLES:
        await websocket.close(CLOSE_FORBIDDEN, "Insufficient permissions")
        return

    token_hash = session.token_hash

    async def check_session() -> bool:
        async with session_factory() as db:
            return await auth_service.is_session_active(db, token_hash)

    connection = Connection(
        websocket,
        user_id=session.user.id,
        session_token_hash=token_hash,
        session_expires_at=session.expires_at,
        check_session=check_session,
        heartbeat_seconds=settings.ws_heartbeat_seconds,
        max_pending=settings.ws_max_pending_messages,
        current_seq=lambda: hub.seq,
    )
    hub.add(connection)
    try:
        await connection.run(
            {
                "user": UserOut.model_validate(session.user).model_dump(mode="json"),
                "heartbeat_seconds": settings.ws_heartbeat_seconds,
            }
        )
    finally:
        hub.remove(connection)
