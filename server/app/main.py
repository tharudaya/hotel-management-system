"""App factory.

Run with `uvicorn app.main:create_app --factory`; tests call `create_app(settings)`.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.router import api_router
from app.api.routes import realtime
from app.core.config import Settings, get_settings
from app.core.email import create_email_sender
from app.core.security_middleware import SecurityMiddleware
from app.db.session import create_engine, create_session_factory
from app.realtime.bus import EventBus
from app.realtime.hub import ConnectionManager
from app.services.errors import (
    DomainError,
    GoneError,
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
)
from app.services.permissions import sync_permission_catalog

logger: logging.Logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        app.state.session_factory = create_session_factory(engine)
        # Make the permissions table match the code catalog (app/core/permissions.py).
        # A database outage must not stop the app from starting (and /api/health from
        # reporting it); access checks use the code catalog either way.
        try:
            async with app.state.session_factory() as db:
                await sync_permission_catalog(db)
        except SQLAlchemyError, OSError:
            logger.exception("Permission catalog sync failed; continuing")
        try:
            yield
        finally:
            hub.close_all()
            await engine.dispose()

    # The interactive docs and schema describe every endpoint; development only.
    expose_docs = settings.environment != "production"
    app = FastAPI(
        title="Hotel Management System",
        lifespan=lifespan,
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
        openapi_url="/openapi.json" if expose_docs else None,
    )
    app.state.settings = settings
    # In-process only; see the scaling note in app/realtime/hub.py.
    bus = EventBus()
    hub = ConnectionManager()
    bus.subscribe(hub.handle_event)
    app.state.event_bus = bus
    app.state.email_sender = create_email_sender(settings)
    app.state.hub = hub

    app.add_middleware(SecurityMiddleware, allowed_origins=settings.cors_origins)
    # Added last, so outermost: answers CORS preflights before anything else.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # Session cookies must be sent cross-origin from the Vite dev server.
        # With credentials allowed, origins must be listed explicitly (never "*").
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    app.include_router(api_router)
    app.include_router(realtime.router)  # /ws, outside /api

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        # Not found -> 404, missing permission -> 403, expired/used -> 410, rule-breaking
        # input -> 422, any other business-rule
        # rejection -> 409 Conflict. "code" lets clients react without parsing text.
        if isinstance(exc, NotFoundError):
            status_code = status.HTTP_404_NOT_FOUND
        elif isinstance(exc, PermissionDeniedError):
            status_code = status.HTTP_403_FORBIDDEN
        elif isinstance(exc, GoneError):
            status_code = status.HTTP_410_GONE
        elif isinstance(exc, InvalidInputError):
            status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
        else:
            status_code = status.HTTP_409_CONFLICT
        return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=status_code)

    return app
