"""App factory.

Run with `uvicorn app.main:create_app --factory`; tests call `create_app(settings)`.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.api.routes import realtime
from app.core.config import Settings, get_settings
from app.db.session import create_engine, create_session_factory
from app.realtime.bus import EventBus
from app.realtime.hub import ConnectionManager
from app.services.errors import DomainError, NotFoundError


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        app.state.session_factory = create_session_factory(engine)
        try:
            yield
        finally:
            hub.close_all()
            await engine.dispose()

    app = FastAPI(title="Hotel Management System", lifespan=lifespan)
    app.state.settings = settings
    # In-process only; see the scaling note in app/realtime/hub.py.
    bus = EventBus()
    hub = ConnectionManager()
    bus.subscribe(hub.handle_event)
    app.state.event_bus = bus
    app.state.hub = hub

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
        # Not found -> 404; every other business-rule rejection -> 409 Conflict.
        # "code" lets clients react without parsing the message.
        status_code = (
            status.HTTP_404_NOT_FOUND
            if isinstance(exc, NotFoundError)
            else status.HTTP_409_CONFLICT
        )
        return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=status_code)

    return app
