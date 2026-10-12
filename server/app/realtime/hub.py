"""Realtime hub: tracks WebSocket clients and pushes domain events to them.

Wire protocol (server -> client only; the socket never accepts writes):

    {"type": "<message type>", "seq": <int>, "payload": {...}}

    hello                on connect; payload {"user", "heartbeat_seconds"}
    room_status_changed  payload {"business_date", "room": <dashboard entry>}
    summary_changed      payload {"summary": <dashboard summary>}
    ping                 heartbeat; payload {}

`seq` increases by one for every broadcast event (room_status_changed,
summary_changed). hello and ping carry the latest seq without consuming one,
so a client that sees a jump has missed events and should refetch over REST.
Clients connect first, then fetch the REST snapshot, then apply events.

The only message a client may send is {"type": "pong"}, in reply to ping;
anything else is ignored.

Close codes: 4401 not authenticated / session ended, 4403 forbidden,
4408 heartbeat timeout, 1013 client too slow, 1001 server shutting down.

SCALING NOTE: events come from the in-process EventBus, so a client only hears
about changes made through the same server process, and `seq` is per process.
Before running more than one server instance (or uvicorn worker), publish
events through a shared channel (Redis pub/sub, or Postgres LISTEN/NOTIFY
since we already run Postgres) and have every instance's hub subscribe to it;
`seq` must then come from that shared source (e.g. a Postgres sequence or a
Redis INCR) to stay monotonic across instances.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Final

from fastapi import WebSocket, WebSocketDisconnect

from app.realtime.events import (
    BroadcastEvent,
    DomainEvent,
    PermissionsChanged,
    SessionEnded,
    broadcast_payload,
)

logger: logging.Logger = logging.getLogger(__name__)

CLOSE_UNAUTHENTICATED: Final = 4401
CLOSE_FORBIDDEN: Final = 4403
CLOSE_HEARTBEAT_TIMEOUT: Final = 4408
CLOSE_TOO_SLOW: Final = 1013
CLOSE_GOING_AWAY: Final = 1001


def envelope(message_type: str, seq: int, payload: dict[str, Any]) -> str:
    return json.dumps({"type": message_type, "seq": seq, "payload": payload})


class Access(Enum):
    """Result of re-checking whether a connected user may still receive events."""

    OK = "ok"
    SESSION_ENDED = "session_ended"  # logged out, revoked, deactivated or expired
    FORBIDDEN = "forbidden"  # signed in, but no longer has dashboard.view


@dataclass(frozen=True)
class _CloseRequest:
    code: int
    reason: str


class Connection:
    """One connected client. Runs three tasks until any of them ends:

    - sender: the only writer to the socket; drains this connection's queue
    - receiver: reads pongs and notices client disconnects
    - heartbeat: pings, enforces pong timeout, session expiry, and re-checks access
      (session and dashboard.view) on every beat and whenever recheck() is called
    """

    def __init__(
        self,
        websocket: WebSocket,
        *,
        user_id: int,
        property_id: int,
        session_token_hash: str,
        session_expires_at: datetime,
        check_access: Callable[[], Awaitable[Access]],
        heartbeat_seconds: float,
        max_pending: int,
        current_seq: Callable[[], int],
    ) -> None:
        self.websocket = websocket
        self.user_id = user_id
        self.property_id = property_id
        self.session_token_hash = session_token_hash
        self._session_expires_at = session_expires_at
        self._check_access = check_access
        self._recheck_requested = asyncio.Event()
        self._heartbeat_seconds = heartbeat_seconds
        self._max_pending = max_pending
        self._current_seq = current_seq
        self._queue: asyncio.Queue[str | _CloseRequest] = asyncio.Queue()
        self._closing = False
        self._last_pong = time.monotonic()

    @property
    def is_closing(self) -> bool:
        return self._closing

    def send(self, text: str) -> None:
        """Queue a message without waiting. A client that falls too far behind is dropped."""
        if self._closing:
            return
        if self._queue.qsize() >= self._max_pending:
            self.close(CLOSE_TOO_SLOW, "Client too slow; reconnect and refetch")
            return
        self._queue.put_nowait(text)

    def recheck(self) -> None:
        """Re-check access now instead of at the next heartbeat (never blocks)."""
        self._recheck_requested.set()

    def close(self, code: int, reason: str) -> None:
        """Ask the connection to close after the messages already queued."""
        if not self._closing:
            self._closing = True
            self._queue.put_nowait(_CloseRequest(code, reason))

    async def run(self, hello_payload: dict[str, Any]) -> None:
        self._queue.put_nowait(envelope("hello", self._current_seq(), hello_payload))
        tasks = [
            asyncio.create_task(self._sender()),
            asyncio.create_task(self._receiver()),
            asyncio.create_task(self._heartbeat()),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        # Surface unexpected errors (not disconnects) in the logs.
        for task in done:
            if not task.cancelled() and (exc := task.exception()) is not None:
                logger.error("WebSocket task failed", exc_info=exc)

    async def _sender(self) -> None:
        while True:
            item = await self._queue.get()
            if isinstance(item, _CloseRequest):
                # Suppressed: the client may already have closed the socket.
                with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                    await self.websocket.close(item.code, item.reason)
                return
            try:
                await self.websocket.send_text(item)
            except RuntimeError, WebSocketDisconnect:
                return  # client went away

    async def _receiver(self) -> None:
        try:
            while True:
                text = await self.websocket.receive_text()
                try:
                    message = json.loads(text)
                except ValueError:
                    continue
                if isinstance(message, dict) and message.get("type") == "pong":
                    self._last_pong = time.monotonic()
        except WebSocketDisconnect, RuntimeError:
            return

    async def _heartbeat(self) -> None:
        while True:
            until_expiry = (self._session_expires_at - datetime.now(UTC)).total_seconds()
            wait = max(min(self._heartbeat_seconds, until_expiry), 0)
            # Sleep until the next beat, or wake early when a recheck is requested.
            try:
                await asyncio.wait_for(self._recheck_requested.wait(), timeout=wait)
                recheck_only = True
            except TimeoutError:
                recheck_only = False
            self._recheck_requested.clear()

            if datetime.now(UTC) >= self._session_expires_at:
                self.close(CLOSE_UNAUTHENTICATED, "Session expired")
                return
            access = await self._check_access()
            if access is Access.SESSION_ENDED:
                self.close(CLOSE_UNAUTHENTICATED, "Session ended")
                return
            if access is Access.FORBIDDEN:
                self.close(CLOSE_FORBIDDEN, "Insufficient permissions")
                return
            if recheck_only:
                continue  # not a heartbeat: no ping, no pong bookkeeping
            if time.monotonic() - self._last_pong > 2 * self._heartbeat_seconds:
                self.close(CLOSE_HEARTBEAT_TIMEOUT, "No pong received")
                return
            self.send(envelope("ping", self._current_seq(), {}))


class ConnectionManager:
    """Tracks connected clients and fans out events from the event bus."""

    def __init__(self) -> None:
        self._connections: set[Connection] = set()
        self._seq = 0

    @property
    def seq(self) -> int:
        """Sequence number of the latest broadcast event (0 before the first)."""
        return self._seq

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def add(self, connection: Connection) -> None:
        self._connections.add(connection)

    def remove(self, connection: Connection) -> None:
        self._connections.discard(connection)

    async def handle_event(self, event: DomainEvent) -> None:
        """EventBus subscriber. Never blocks on slow clients: it only queues."""
        if isinstance(event, SessionEnded):
            for connection in list(self._connections):
                if connection.session_token_hash == event.session_token_hash:
                    connection.close(CLOSE_UNAUTHENTICATED, event.reason)
            return
        if isinstance(event, PermissionsChanged):
            affected = None if event.user_ids is None else set(event.user_ids)
            for connection in list(self._connections):
                if connection.property_id == event.property_id and (
                    affected is None or connection.user_id in affected
                ):
                    connection.recheck()
            return
        self.broadcast(event)

    def broadcast(self, event: BroadcastEvent) -> None:
        self._seq += 1
        text = envelope(event.type, self._seq, broadcast_payload(event))
        for connection in list(self._connections):
            connection.send(text)

    def close_all(self, code: int = CLOSE_GOING_AWAY, reason: str = "Server shutting down") -> None:
        for connection in list(self._connections):
            connection.close(code, reason)
