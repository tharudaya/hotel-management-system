"""Admin changes reach open sessions and WebSockets at once, not at the next
heartbeat (25 s) or the next login.

Each test opens a REST session and a WebSocket for the front desk user, makes the
change through the admin API from another client, then checks both.
"""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.permissions import P
from app.models import Role, RolePermission, UserPropertyRole
from app.services.permissions import get_default_property_id
from tests.conftest import LiveClientFactory, Roles, live_clients, role_id
from tests.test_realtime import connect, expect_close, live, login, receive, seeded

__all__ = ["seeded"]  # the fixture, re-used from the realtime tests

# Well under the 25 s heartbeat: the change is pushed, not polled.
IMMEDIATE_SECONDS = 1.0


@asynccontextmanager
async def two_clients(
    settings: Settings,
) -> AsyncIterator[tuple[AsyncClient, AsyncClient, LiveClientFactory]]:
    """(front desk user, admin), both signed in, on one app."""
    async with live_clients(settings) as make:
        app, desk = await live(make)  # Receptionist
        _, admin = await make(app)
        await login(admin, Roles.ADMIN)
        yield desk, admin, make


async def desk_user_id(desk: AsyncClient) -> int:
    me = await desk.get("/api/auth/me")
    assert me.status_code == 200
    user_id: int = me.json()["id"]
    return user_id


async def test_deactivation_ends_session_and_socket_at_once(
    test_settings: Settings, seeded: dict[str, int]
) -> None:
    async with two_clients(test_settings) as (desk, admin, _):
        user_id = await desk_user_id(desk)
        async with connect(desk) as ws:
            await receive(ws)  # hello
            started = time.monotonic()

            response = await admin.post(f"/api/admin/users/{user_id}/deactivate")
            assert response.status_code == 200, response.text

            await expect_close(ws, 4401)
            assert time.monotonic() - started < IMMEDIATE_SECONDS
        assert (await desk.get("/api/auth/me")).status_code == 401
        assert (await desk.get("/api/dashboard/rooms")).status_code == 401


async def test_role_change_ends_session_and_socket_at_once(
    test_settings: Settings, seeded: dict[str, int], live_db: async_sessionmaker[AsyncSession]
) -> None:
    async with live_db() as db:
        attendant = await role_id(db, Roles.ROOM_ATTENDANT)
    async with two_clients(test_settings) as (desk, admin, _):
        user_id = await desk_user_id(desk)
        async with connect(desk) as ws:
            await receive(ws)
            started = time.monotonic()

            response = await admin.put(
                f"/api/admin/users/{user_id}/role", json={"role_id": attendant}
            )
            assert response.status_code == 200, response.text

            await expect_close(ws, 4401, "Session revoked")
            assert time.monotonic() - started < IMMEDIATE_SECONDS
        # A role change signs the user out; they must sign in again to get the new role.
        assert (await desk.get("/api/auth/me")).status_code == 401


async def test_revoking_sessions_ends_session_and_socket_at_once(
    test_settings: Settings, seeded: dict[str, int]
) -> None:
    async with two_clients(test_settings) as (desk, admin, _):
        user_id = await desk_user_id(desk)
        async with connect(desk) as ws:
            await receive(ws)
            await admin.post(f"/api/admin/users/{user_id}/revoke-sessions")

            await expect_close(ws, 4401)
        assert (await desk.get("/api/auth/me")).status_code == 401


async def test_role_permission_changes_apply_to_open_sessions_at_once(
    test_settings: Settings, seeded: dict[str, int], live_db: async_sessionmaker[AsyncSession]
) -> None:
    """Editing a role does not sign anyone out: the next request already sees the new
    permissions, the socket is told to refetch, and losing dashboard access closes it."""
    async with live_db() as db:
        custom = Role(
            property_id=await get_default_property_id(db), name="Desk trial", is_system=False
        )
        db.add(custom)
        await db.flush()
        db.add_all(
            RolePermission(role_id=custom.id, permission_key=k)
            for k in (P.DASHBOARD_VIEW, P.RESERVATION_VIEW)
        )
        await db.commit()
        custom_id = custom.id
    try:
        async with two_clients(test_settings) as (desk, admin, _):
            user_id = await desk_user_id(desk)
            assert (
                await admin.put(f"/api/admin/users/{user_id}/role", json={"role_id": custom_id})
            ).status_code == 200
            await login(desk, Roles.RECEPTIONIST)  # the role change signed them out
            assert (await desk.get(f"/api/reservations/{seeded['reservation']}")).status_code == 200

            async with connect(desk) as ws:
                await receive(ws)

                # Take reservation.view away: the very next request is refused.
                await admin.patch(
                    f"/api/admin/roles/{custom_id}", json={"permissions": [P.DASHBOARD_VIEW]}
                )
                assert (await receive(ws))["type"] == "permissions_changed"
                assert (
                    await desk.get(f"/api/reservations/{seeded['reservation']}")
                ).status_code == 403
                me = (await desk.get("/api/auth/me")).json()
                assert me["permissions"] == [P.DASHBOARD_VIEW]

                # Take dashboard.view away: the socket closes at once; the session stays.
                started = time.monotonic()
                await admin.patch(f"/api/admin/roles/{custom_id}", json={"permissions": []})
                await expect_close(ws, 4403, "Insufficient permissions")
                assert time.monotonic() - started < IMMEDIATE_SECONDS
            assert (await desk.get("/api/auth/me")).status_code == 200
            assert (await desk.get("/api/dashboard/rooms")).status_code == 403
    finally:
        async with live_db() as db:  # roles survive the table truncation; clean up
            await db.execute(delete(UserPropertyRole).where(UserPropertyRole.role_id == custom_id))
            await db.execute(delete(Role).where(Role.id == custom_id))
            await db.commit()


async def test_expiring_a_user_ends_session_and_socket_at_once(
    test_settings: Settings, seeded: dict[str, int]
) -> None:
    async with two_clients(test_settings) as (desk, admin, _):
        user_id = await desk_user_id(desk)
        async with connect(desk) as ws:
            await receive(ws)

            # An expiry in the past is applied at once (the admin cannot expire
            # themselves, but can expire a receptionist).
            response = await admin.patch(
                f"/api/admin/users/{user_id}", json={"expires_at": "2020-01-01T00:00:00Z"}
            )
            assert response.status_code == 200, response.text

            await expect_close(ws, 4401)
        assert (await desk.get("/api/auth/me")).status_code == 401
