"""WebSocket hub: auth, event delivery, heartbeat, session end.

These tests use committed data (see `live_db` in conftest) because the socket
and the REST calls run concurrently on separate database sessions.
"""

import asyncio
import json
from collections.abc import AsyncIterator, MutableMapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi import FastAPI, WebSocket
from httpx import AsyncClient
from httpx_ws import AsyncWebSocketSession, WebSocketDisconnect, aconnect_ws
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_hotel_today
from app.core.config import Settings
from app.core.permissions import P
from app.main import create_app
from app.models import (
    HousekeepingStatus,
    ReservationStatus,
    Role,
    RolePermission,
    Room,
    RoomType,
    User,
    UserPropertyRole,
    UserSession,
)
from app.realtime.hub import Access, Connection
from app.services.permissions import get_default_property_id, publish_permissions_changed
from tests.conftest import (
    USERNAME_FOR_ROLE,
    LiveClientFactory,
    Roles,
    assign_role,
    cached_password_hash,
    live_clients,
    role_id,
)
from tests.factories import new_reservation

TODAY: date = date(2026, 3, 10)
WS_URL: str = "https://test/ws"


@pytest.fixture
async def seeded(live_db: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """A user per default role, one in-house guest in room 101 and a dirty room 102."""
    async with live_db() as db:
        for role_name, username in USERNAME_FOR_ROLE.items():
            user = User(
                username=username,
                password_hash=cached_password_hash(f"{username}-password"),
                full_name=username.title(),
            )
            db.add(user)
            await db.flush()
            await assign_role(db, user, role_name)
        pid = await get_default_property_id(db)
        std = RoomType(property_id=pid, code="STD", name="Standard", max_occupancy=2)
        room_101 = Room(
            property_id=pid,
            number="101",
            floor=1,
            room_type=std,
            housekeeping_status=HousekeepingStatus.CLEAN,
        )
        room_102 = Room(
            property_id=pid,
            number="102",
            floor=1,
            room_type=std,
            housekeeping_status=HousekeepingStatus.DIRTY,
        )
        guest = new_reservation(
            std,
            room=room_101,
            check_in_date=TODAY - timedelta(days=2),
            check_out_date=TODAY,
            status=ReservationStatus.CHECKED_IN,
        )
        db.add_all([room_101, room_102, guest])
        await db.commit()
        return {"room_101": room_101.id, "room_102": room_102.id, "reservation": guest.id}


async def login(client: AsyncClient, role: str) -> None:
    username = USERNAME_FOR_ROLE[role]
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": f"{username}-password"}
    )
    assert response.status_code == 200, response.text


async def live(
    make: LiveClientFactory, role: str | None = Roles.RECEPTIONIST, **settings: object
) -> tuple[FastAPI, AsyncClient]:
    app, client = await make(**settings)
    app.dependency_overrides[get_hotel_today] = lambda: TODAY
    if role is not None:
        await login(client, role)
    return app, client


@asynccontextmanager
async def live_db_session(app: FastAPI) -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as db:
        yield db


@asynccontextmanager
async def connect(
    client: AsyncClient, headers: dict[str, str] | None = None
) -> AsyncIterator[AsyncWebSocketSession]:
    # Disable the library's protocol-level pings; we test our own heartbeat.
    session: AbstractAsyncContextManager[AsyncWebSocketSession] = aconnect_ws(
        WS_URL, client, keepalive_ping_interval_seconds=None, headers=headers or {}
    )
    async with session as ws:
        yield ws


async def receive(ws: AsyncWebSocketSession, *, skip_pings: bool = True) -> dict[str, Any]:
    while True:
        message = cast(dict[str, Any], json.loads(await ws.receive_text(timeout=3)))
        if not (skip_pings and message["type"] == "ping"):
            return message


async def expect_close(ws: AsyncWebSocketSession, code: int, reason: str | None = None) -> None:
    with pytest.raises(WebSocketDisconnect) as exc_info:
        while True:  # drain anything queued before the close
            await ws.receive_text(timeout=3)
    assert exc_info.value.code == code
    if reason is not None:
        assert exc_info.value.reason == reason


# --- the end-to-end case -------------------------------------------------------------


async def test_check_out_over_rest_is_pushed_to_websocket(
    test_settings: Settings, seeded: dict[str, int]
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        _, client = await live(live_client_factory)

        async with connect(client) as ws:
            hello = await receive(ws)
            user = hello["payload"]["user"]
            assert (hello["type"], hello["seq"], hello["payload"]["heartbeat_seconds"]) == (
                "hello",
                0,
                25.0,
            )
            assert (user["username"], user["full_name"], user["role"]["name"]) == (
                "frontdesk",
                "Frontdesk",
                "Receptionist",
            )
            assert "dashboard.view" in user["permissions"]

            response = await client.post(
                f"/api/reservations/{seeded['reservation']}/check-out", json={"room_version": 1}
            )
            assert response.status_code == 200, response.text

            # First what happened to the booking (no guest data), then the room, then
            # the summary.
            reservation_event = await receive(ws)
            assert (reservation_event["type"], reservation_event["seq"]) == (
                "reservation_changed",
                1,
            )
            assert reservation_event["payload"]["change"] == "checked_out"
            assert reservation_event["payload"]["reservation_id"] == seeded["reservation"]

            room_event = await receive(ws)
            assert room_event == {
                "type": "room_status_changed",
                "seq": 2,
                "payload": {"business_date": "2026-03-10", "room": response.json()},
            }
            assert room_event["payload"]["room"]["occupancy_status"] == "vacant"
            assert room_event["payload"]["room"]["housekeeping_status"] == "dirty"

            summary_event = await receive(ws)
            assert summary_event["type"] == "summary_changed"
            assert summary_event["seq"] == 3
            summary = summary_event["payload"]["summary"]
            assert (summary["in_house"], summary["departures_checked_out"]) == (0, 1)


async def test_room_setup_changes_tell_dashboards_to_refetch(
    test_settings: Settings, seeded: dict[str, int]
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        app, desk = await live(live_client_factory)  # Receptionist watching the dashboard
        _, admin = await live_client_factory(app)
        await login(admin, Roles.ADMIN)
        async with live_db_session(app) as db:
            std = await db.scalar(select(RoomType.id).where(RoomType.code == "STD"))

        async with connect(desk) as ws:
            hello = await receive(ws)
            response = await admin.post(
                "/api/admin/rooms", json={"number": "103", "floor": 1, "room_type_id": std}
            )
            assert response.status_code == 201, response.text

            message = await receive(ws)
            assert message["type"] == "room_setup_changed"
            assert message["seq"] == hello["seq"] + 1  # a broadcast: it consumes a seq
            assert set(message["payload"]) == {"property_id"}


async def test_sequence_numbers_increase_across_events_and_clients(
    test_settings: Settings, seeded: dict[str, int]
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        app, front_desk = await live(live_client_factory)
        _, manager = await live_client_factory(app)
        await login(manager, Roles.GENERAL_MANAGER)
        _, housekeeper = await live_client_factory(app)
        await login(housekeeper, Roles.ROOM_ATTENDANT)

        async with connect(front_desk) as ws_a, connect(manager) as ws_b:
            await receive(ws_a)
            await receive(ws_b)

            room = f"/api/rooms/{seeded['room_102']}/housekeeping-status"
            assert (
                await housekeeper.patch(room, json={"status": "in_progress", "version": 1})
            ).status_code == 200
            assert (
                await housekeeper.patch(room, json={"status": "clean", "version": 2})
            ).status_code == 200

            received_a = [await receive(ws_a) for _ in range(4)]
            received_b = [await receive(ws_b) for _ in range(4)]

        assert [m["seq"] for m in received_a] == [1, 2, 3, 4]
        assert [m["type"] for m in received_a] == [
            "room_status_changed",
            "summary_changed",
            "room_status_changed",
            "summary_changed",
        ]
        assert received_b == received_a
        assert received_a[2]["payload"]["room"]["housekeeping_status"] == "clean"


async def test_late_joiner_hello_carries_current_seq(
    test_settings: Settings, seeded: dict[str, int]
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        _, client = await live(live_client_factory)
        await client.post(
            f"/api/reservations/{seeded['reservation']}/check-out", json={"room_version": 1}
        )

        async with connect(client) as ws:
            hello = await receive(ws)

        # reservation_changed, room_status_changed, summary_changed
        assert (hello["type"], hello["seq"]) == ("hello", 3)


# --- authentication and authorization --------------------------------------------------


async def test_rejects_connection_without_session(
    test_settings: Settings, seeded: dict[str, int]
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        _, client = await live(live_client_factory, role=None)

        async with connect(client) as ws:
            await expect_close(ws, 4401, "Not authenticated")


async def test_rejects_forged_session_cookie(
    test_settings: Settings, seeded: dict[str, int]
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        app, client = await live(live_client_factory, role=None)
        client.cookies.set(app.state.settings.session_cookie_name, "forged")

        async with connect(client) as ws:
            await expect_close(ws, 4401)


async def test_rejects_role_without_dashboard_access(
    test_settings: Settings, seeded: dict[str, int]
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        _, client = await live(live_client_factory, role=Roles.ROOM_ATTENDANT)

        async with connect(client) as ws:
            await expect_close(ws, 4403, "Insufficient permissions")


@pytest.mark.parametrize(
    "role",
    [
        Roles.ADMIN,
        Roles.GENERAL_MANAGER,
        Roles.FRONT_OFFICE_MANAGER,
        Roles.RECEPTIONIST,
        Roles.HOUSEKEEPING_SUPERVISOR,
    ],
)
async def test_accepts_dashboard_roles(
    test_settings: Settings, seeded: dict[str, int], role: str
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        _, client = await live(live_client_factory, role=role)

        async with connect(client) as ws:
            assert (await receive(ws))["payload"]["user"]["role"]["name"] == role


async def test_rejects_foreign_origin_before_upgrade(test_settings: Settings) -> None:
    """Drive the ASGI app directly: it must close without ever accepting.

    (A real server turns a close-before-accept into an HTTP 403 handshake reply.)
    No lifespan or database is needed: the origin check runs first.
    """
    app = create_app(test_settings)
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "websocket.connect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "wss",
        "path": "/ws",
        "raw_path": b"/ws",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test"), (b"origin", b"https://evil.example")],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 443),
        "subprotocols": [],
        "state": {},
    }
    await app(scope, receive, send)

    assert [message["type"] for message in sent] == ["websocket.close"]
    assert sent[0]["code"] == 4403


async def test_allows_configured_origin(test_settings: Settings, seeded: dict[str, int]) -> None:
    async with live_clients(test_settings) as live_client_factory:
        app, client = await live(live_client_factory)
        origin = app.state.settings.cors_origins[0]

        async with connect(client, headers={"Origin": origin}) as ws:
            assert (await receive(ws))["type"] == "hello"


# --- session end -----------------------------------------------------------------------


async def test_logout_closes_socket(test_settings: Settings, seeded: dict[str, int]) -> None:
    async with live_clients(test_settings) as live_client_factory:
        app, client = await live(live_client_factory)

        async with connect(client) as ws:
            await receive(ws)
            assert (await client.post("/api/auth/logout")).status_code == 204

            await expect_close(ws, 4401, "Logged out")
        assert app.state.hub.connection_count == 0


async def test_logout_only_closes_that_sessions_sockets(
    test_settings: Settings, seeded: dict[str, int]
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        app, leaving = await live(live_client_factory)
        _, staying = await live_client_factory(app)
        await login(staying, Roles.GENERAL_MANAGER)

        async with connect(leaving) as ws_leaving, connect(staying) as ws_staying:
            await receive(ws_leaving)
            await receive(ws_staying)
            await leaving.post("/api/auth/logout")
            await expect_close(ws_leaving, 4401)

            # The other user's socket still receives events.
            await staying.post(
                f"/api/reservations/{seeded['reservation']}/check-out", json={"room_version": 1}
            )
            assert (await receive(ws_staying))["type"] == "reservation_changed"


async def test_session_expiry_closes_socket(
    test_settings: Settings,
    seeded: dict[str, int],
    live_db: async_sessionmaker[AsyncSession],
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        _, client = await live(live_client_factory)
        async with live_db() as db:
            await db.execute(
                update(UserSession).values(expires_at=datetime.now(UTC) + timedelta(seconds=1.5))
            )
            await db.commit()

        async with connect(client) as ws:
            await receive(ws)
            await expect_close(ws, 4401, "Session expired")


async def test_session_revoked_elsewhere_closes_socket_on_next_heartbeat(
    test_settings: Settings,
    seeded: dict[str, int],
    live_db: async_sessionmaker[AsyncSession],
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        _, client = await live(live_client_factory, ws_heartbeat_seconds=0.2)

        async with connect(client) as ws:
            await receive(ws)
            # E.g. an admin revoking the session directly, without publishing an event.
            async with live_db() as db:
                await db.execute(update(UserSession).values(revoked_at=datetime.now(UTC)))
                await db.commit()

            await expect_close(ws, 4401, "Session ended")


async def test_deactivated_user_socket_closed_on_next_heartbeat(
    test_settings: Settings,
    seeded: dict[str, int],
    live_db: async_sessionmaker[AsyncSession],
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        _, client = await live(live_client_factory, ws_heartbeat_seconds=0.2)

        async with connect(client) as ws:
            await receive(ws)
            async with live_db() as db:
                await db.execute(
                    update(User)
                    .where(User.username == "frontdesk")
                    .values(deactivated_at=datetime.now(UTC))
                )
                await db.commit()

            await expect_close(ws, 4401, "Session ended")


# --- permission changes ------------------------------------------------------------------


async def change_role(
    live_db: async_sessionmaker[AsyncSession], username: str, role_name: str
) -> tuple[int, int]:
    """Give a seeded user another role (committed). Returns (user_id, property_id)."""
    async with live_db() as db:
        property_id = await get_default_property_id(db)
        user_id = await db.scalar(select(User.id).where(User.username == username))
        assert user_id is not None
        await db.execute(
            update(UserPropertyRole)
            .where(UserPropertyRole.user_id == user_id)
            .values(role_id=await role_id(db, role_name))
        )
        await db.commit()
        return user_id, property_id


async def test_socket_closed_at_once_when_the_users_role_loses_dashboard_access(
    test_settings: Settings,
    seeded: dict[str, int],
    live_db: async_sessionmaker[AsyncSession],
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        app, client = await live(live_client_factory)  # Receptionist; heartbeat 25 s

        async with connect(client) as ws:
            await receive(ws)
            user_id, property_id = await change_role(live_db, "frontdesk", Roles.ROOM_ATTENDANT)

            await publish_permissions_changed(app.state.event_bus, property_id, [user_id])

            # Closed by the event, long before the next heartbeat.
            await expect_close(ws, 4403, "Insufficient permissions")


async def test_permission_change_for_another_user_leaves_socket_open(
    test_settings: Settings,
    seeded: dict[str, int],
    live_db: async_sessionmaker[AsyncSession],
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        app, front_desk = await live(live_client_factory)
        _, manager = await live_client_factory(app)
        await login(manager, Roles.GENERAL_MANAGER)

        async with connect(front_desk) as ws_front_desk, connect(manager) as ws_manager:
            await receive(ws_front_desk)
            await receive(ws_manager)
            user_id, property_id = await change_role(live_db, "frontdesk", Roles.ROOM_ATTENDANT)

            await publish_permissions_changed(app.state.event_bus, property_id, [user_id])
            await expect_close(ws_front_desk, 4403)

            # The manager is unaffected and still receives events.
            await manager.post(
                f"/api/reservations/{seeded['reservation']}/check-out", json={"room_version": 1}
            )
            assert (await receive(ws_manager))["type"] == "reservation_changed"


async def test_socket_kept_when_permissions_change_but_dashboard_access_remains(
    test_settings: Settings,
    seeded: dict[str, int],
    live_db: async_sessionmaker[AsyncSession],
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        app, client = await live(live_client_factory)

        async with connect(client) as ws:
            await receive(ws)
            # Receptionist -> Housekeeping supervisor: different permissions, still dashboard.
            user_id, property_id = await change_role(
                live_db, "frontdesk", Roles.HOUSEKEEPING_SUPERVISOR
            )

            await publish_permissions_changed(app.state.event_bus, property_id, [user_id])

            # Told to refetch what it may see; not a broadcast, so no seq is consumed.
            message = await receive(ws)
            assert (message["type"], message["payload"]) == ("permissions_changed", {})
            assert message["seq"] == app.state.hub.seq
            assert app.state.hub.connection_count == 1


async def test_property_wide_change_rechecks_everyone(
    test_settings: Settings,
    seeded: dict[str, int],
    live_db: async_sessionmaker[AsyncSession],
) -> None:
    # A custom role whose dashboard.view is then removed (as if an admin edited it).
    async with live_db() as db:
        property_id = await get_default_property_id(db)
        custom = Role(property_id=property_id, name="Temporary desk", is_system=False)
        db.add(custom)
        await db.flush()
        db.add(RolePermission(role_id=custom.id, permission_key=P.DASHBOARD_VIEW))
        await db.commit()
        custom_id = custom.id
    try:
        await change_role(live_db, "frontdesk", "Temporary desk")
        async with live_clients(test_settings) as live_client_factory:
            app, client = await live(live_client_factory)

            async with connect(client) as ws:
                await receive(ws)
                async with live_db() as db:
                    await db.execute(
                        delete(RolePermission).where(RolePermission.role_id == custom_id)
                    )
                    await db.commit()

                await publish_permissions_changed(app.state.event_bus, property_id, None)

                await expect_close(ws, 4403, "Insufficient permissions")
    finally:
        async with live_db() as db:  # roles survive the table truncation; clean up
            await db.execute(delete(UserPropertyRole).where(UserPropertyRole.role_id == custom_id))
            await db.execute(delete(Role).where(Role.id == custom_id))
            await db.commit()


async def test_editing_a_role_over_rest_reaches_its_holders_live(
    test_settings: Settings,
    seeded: dict[str, int],
    live_db: async_sessionmaker[AsyncSession],
) -> None:
    """PATCH /api/admin/roles/{id}: holders get permissions_changed, then lose access."""
    async with live_db() as db:
        property_id = await get_default_property_id(db)
        custom = Role(property_id=property_id, name="Temporary desk", is_system=False)
        db.add(custom)
        await db.flush()
        db.add(RolePermission(role_id=custom.id, permission_key=P.DASHBOARD_VIEW))
        await db.commit()
        custom_id = custom.id
    try:
        await change_role(live_db, "frontdesk", "Temporary desk")
        async with live_clients(test_settings) as live_client_factory:
            app, front_desk = await live(live_client_factory)
            _, admin = await live_client_factory(app)
            await login(admin, Roles.ADMIN)

            async with connect(front_desk) as ws:
                await receive(ws)

                response = await admin.patch(
                    f"/api/admin/roles/{custom_id}",
                    json={"permissions": [P.DASHBOARD_VIEW, P.RESERVATION_VIEW]},
                )
                assert response.status_code == 200, response.text
                assert (await receive(ws))["type"] == "permissions_changed"
                me = await front_desk.get("/api/auth/me")
                assert P.RESERVATION_VIEW in me.json()["permissions"]

                response = await admin.patch(
                    f"/api/admin/roles/{custom_id}", json={"permissions": [P.RESERVATION_VIEW]}
                )
                assert response.status_code == 200, response.text
                await expect_close(ws, 4403, "Insufficient permissions")
    finally:
        async with live_db() as db:  # roles survive the table truncation; clean up
            await db.execute(delete(UserPropertyRole).where(UserPropertyRole.role_id == custom_id))
            await db.execute(delete(Role).where(Role.id == custom_id))
            await db.commit()


async def test_heartbeat_catches_a_role_change_without_an_event(
    test_settings: Settings,
    seeded: dict[str, int],
    live_db: async_sessionmaker[AsyncSession],
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        _, client = await live(live_client_factory, ws_heartbeat_seconds=0.2)

        async with connect(client) as ws:
            await receive(ws)
            await change_role(live_db, "frontdesk", Roles.ROOM_ATTENDANT)

            await expect_close(ws, 4403, "Insufficient permissions")


@pytest.mark.parametrize(
    ("action", "suffix", "payload"),
    [
        ("deactivate", "/deactivate", None),
        ("role change", "/role", "room attendant"),
        ("revoke sessions", "/revoke-sessions", None),
    ],
)
async def test_admin_actions_close_the_users_socket(
    test_settings: Settings,
    seeded: dict[str, int],
    live_db: async_sessionmaker[AsyncSession],
    action: str,
    suffix: str,
    payload: object,
) -> None:
    async with live_db() as db:
        target_id = await db.scalar(select(User.id).where(User.username == "frontdesk"))
        if payload == "room attendant":
            payload = {"role_id": await role_id(db, Roles.ROOM_ATTENDANT)}
    async with live_clients(test_settings) as live_client_factory:
        app, front_desk = await live(live_client_factory)
        _, admin = await live_client_factory(app)
        await login(admin, Roles.ADMIN)

        async with connect(front_desk) as ws:
            await receive(ws)
            response = await admin.request(
                "PUT" if suffix == "/role" else "POST",
                f"/api/admin/users/{target_id}{suffix}",
                json=payload,
            )
            assert response.status_code == 200, (action, response.text)

            await expect_close(ws, 4401, "Session revoked")


# --- heartbeat -------------------------------------------------------------------------


async def test_heartbeat_closes_silent_client(
    test_settings: Settings, seeded: dict[str, int]
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        app, client = await live(live_client_factory, ws_heartbeat_seconds=0.2)

        async with connect(client) as ws:
            hello = await receive(ws)
            assert hello["payload"]["heartbeat_seconds"] == 0.2
            ping = await receive(ws, skip_pings=False)
            assert ping == {"type": "ping", "seq": 0, "payload": {}}
            # Never answer: closed after two intervals without a pong.
            await expect_close(ws, 4408, "No pong received")

        for _ in range(50):  # the server task finishes just after the close frame
            if app.state.hub.connection_count == 0:
                break
            await asyncio.sleep(0.02)
        assert app.state.hub.connection_count == 0


async def test_heartbeat_keeps_responsive_client(
    test_settings: Settings, seeded: dict[str, int]
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        _, client = await live(live_client_factory, ws_heartbeat_seconds=0.2)

        async with connect(client) as ws:
            await receive(ws)
            pings = 0
            while pings < 6:  # 1.2 s: well past the 0.4 s timeout
                message = await receive(ws, skip_pings=False)
                assert message["type"] == "ping"
                pings += 1
                await ws.send_text(json.dumps({"type": "pong"}))


async def test_client_messages_other_than_pong_are_ignored(
    test_settings: Settings, seeded: dict[str, int]
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        app, client = await live(live_client_factory)

        async with connect(client) as ws:
            await receive(ws)
            await ws.send_text(
                json.dumps({"type": "check_out", "reservation_id": seeded["reservation"]})
            )
            await ws.send_text("not json")
            # Still connected and nothing changed.
            await client.post(
                f"/api/rooms/{seeded['room_102']}/housekeeping-status"
            )  # 422/403 noise
            assert app.state.hub.connection_count == 1


async def test_client_disconnect_removes_connection(
    test_settings: Settings, seeded: dict[str, int]
) -> None:
    async with live_clients(test_settings) as live_client_factory:
        app, client = await live(live_client_factory)

        async with connect(client) as ws:
            await receive(ws)
            assert app.state.hub.connection_count == 1

        for _ in range(50):
            if app.state.hub.connection_count == 0:
                break
            await asyncio.sleep(0.02)
        assert app.state.hub.connection_count == 0


# --- unit: slow consumers --------------------------------------------------------------


async def test_slow_client_is_dropped_instead_of_buffering_forever() -> None:
    connection = Connection(
        cast(WebSocket, object()),  # never used: run() is not started
        user_id=1,
        property_id=1,
        session_token_hash="h",
        session_expires_at=datetime.now(UTC) + timedelta(hours=1),
        check_access=lambda: asyncio.sleep(0, result=Access.OK),
        heartbeat_seconds=25,
        max_pending=3,
        current_seq=lambda: 0,
    )

    for i in range(3):
        connection.send(f"message {i}")
    assert not connection.is_closing
    connection.send("one too many")

    assert connection.is_closing
