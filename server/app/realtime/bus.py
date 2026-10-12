"""In-process publish/subscribe for domain events.

Services publish after their transaction commits; the realtime hub subscribes
and fans events out to WebSocket clients.

SINGLE PROCESS ONLY: subscribers only see events published in the same
process. When we run multiple server instances (or uvicorn workers), this bus
must forward events through Redis pub/sub or Postgres LISTEN/NOTIFY so every
instance's hub receives every change. See app/realtime/hub.py.
"""

import logging
from collections.abc import Awaitable, Callable

from app.realtime.events import DomainEvent

logger: logging.Logger = logging.getLogger(__name__)

type EventHandler = Callable[[DomainEvent], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        """Register a handler; returns a function that unsubscribes it."""
        self._handlers.append(handler)

        def unsubscribe() -> None:
            if handler in self._handlers:
                self._handlers.remove(handler)

        return unsubscribe

    async def publish(self, event: DomainEvent) -> None:
        """Deliver to every handler in order.

        Handlers should be quick (e.g. enqueue and return). A failing handler
        is logged and does not stop the others or the caller: the change it
        reports is already committed.
        """
        for handler in list(self._handlers):
            try:
                await handler(event)
            except Exception:
                logger.exception("Event handler %r failed for %s", handler, event.type)
