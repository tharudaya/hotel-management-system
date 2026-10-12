from datetime import UTC, datetime, timedelta
from typing import Annotated

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.config import Settings
from app.core.permissions import DEFAULT_ROLES, P
from app.models import AuditEvent, AuditEventType, Property, User, UserSession
from app.services import permissions as permission_service
from app.services.permissions import get_default_property_id
from tests.conftest import USERNAME_FOR_ROLE, LoginAs, MakeUser, Roles

LOGIN: str = "/api/auth/login"
LOGOUT: str = "/api/auth/logout"
ME: str = "/api/auth/me"
GENERIC_LOGIN_ERROR: dict[str, str] = {"detail": "Invalid username or password"}


async def property_json(db: AsyncSession) -> dict[str, object]:
    prop = await db.get(Property, await get_default_property_id(db))
    assert prop is not None
    return {"id": prop.id, "name": prop.name, "time_zone": prop.time_zone}


def permissions_of(role_name: str) -> list[str]:
    [role] = [r for r in DEFAULT_ROLES if r.name == role_name]
    return sorted(role.permissions)


async def login(
    client: AsyncClient, username: str = "frontdesk", password: str = "frontdesk-password"
) -> httpx.Response:
    return await client.post(LOGIN, json={"username": username, "password": password})


async def audit_events(db: AsyncSession) -> list[AuditEvent]:
    return list((await db.scalars(select(AuditEvent).order_by(AuditEvent.id))).all())


async def sessions(db: AsyncSession) -> list[UserSession]:
    return list((await db.scalars(select(UserSession).order_by(UserSession.id))).all())


# --- login -------------------------------------------------------------------


async def test_login_success_sets_session_cookie(
    client: AsyncClient, make_user: MakeUser, test_settings: Settings, db_session: AsyncSession
) -> None:
    user = await make_user("frontdesk")

    response = await login(client)

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "id": user.id,
        "username": "frontdesk",
        "full_name": "Frontdesk",
        "property": await property_json(db_session),
        "role": {"id": body["role"]["id"], "name": "Receptionist"},
        "permissions": permissions_of(Roles.RECEPTIONIST),
    }
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{test_settings.session_cookie_name}=")
    for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=28800"):
        assert attribute in set_cookie

    token = response.cookies[test_settings.session_cookie_name]
    [stored] = await sessions(db_session)
    assert stored.user_id == user.id
    assert stored.revoked_at is None
    assert token not in stored.token_hash  # only a keyed hash is stored

    [event] = await audit_events(db_session)
    assert event.event_type == AuditEventType.LOGIN_SUCCEEDED
    assert event.user_id == user.id
    assert event.ip_address == "127.0.0.1"


async def test_login_username_is_case_and_whitespace_insensitive(
    client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("frontdesk")

    response = await login(client, username="  FrontDesk ")

    assert response.status_code == 200


async def test_login_wrong_password(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    user = await make_user("frontdesk")

    response = await login(client, password="wrong-password")

    assert response.status_code == 401
    assert response.json() == GENERIC_LOGIN_ERROR
    assert "set-cookie" not in response.headers
    assert await sessions(db_session) == []
    [event] = await audit_events(db_session)
    assert event.event_type == AuditEventType.LOGIN_FAILED
    assert event.user_id == user.id
    assert event.details == {"reason": "bad_password"}


async def test_login_unknown_user_gets_same_error(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await login(client, username="nobody")

    assert response.status_code == 401
    assert response.json() == GENERIC_LOGIN_ERROR
    [event] = await audit_events(db_session)
    assert event.event_type == AuditEventType.LOGIN_FAILED
    assert (event.user_id, event.username) == (None, "nobody")
    assert event.details == {"reason": "unknown_user"}


async def test_login_deactivated_user_rejected_with_same_error(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await make_user("frontdesk", deactivated=True)

    response = await login(client)  # correct password

    assert response.status_code == 401
    assert response.json() == GENERIC_LOGIN_ERROR
    assert "set-cookie" not in response.headers
    assert await sessions(db_session) == []
    [event] = await audit_events(db_session)
    assert event.details == {"reason": "deactivated"}


async def test_login_expired_user_rejected_with_same_error(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await make_user("frontdesk", expires_at=datetime.now(UTC) - timedelta(minutes=1))

    response = await login(client)  # correct password

    assert response.status_code == 401
    assert response.json() == GENERIC_LOGIN_ERROR
    assert await sessions(db_session) == []
    [event] = await audit_events(db_session)
    assert event.details == {"reason": "expired"}


async def test_login_allowed_before_expiry_and_records_last_login(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    user = await make_user("frontdesk", expires_at=datetime.now(UTC) + timedelta(days=1))
    assert user.last_login_at is None

    assert (await login(client)).status_code == 200

    await db_session.refresh(user)
    assert user.last_login_at is not None


@pytest.mark.parametrize(
    "body",
    [
        {"username": "frontdesk"},
        {"password": "x"},
        {"username": "", "password": "x"},
        {"username": "frontdesk", "password": ""},
        {"username": "frontdesk", "password": "x" * 1025},
        {"username": "x" * 51, "password": "x"},
    ],
)
async def test_login_validation_errors(client: AsyncClient, body: dict[str, str]) -> None:
    response = await client.post(LOGIN, json=body)

    assert response.status_code == 422


async def test_login_throttled_after_repeated_failures_for_username(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await make_user("frontdesk")
    for _ in range(5):
        assert (await login(client, password="wrong")).status_code == 401

    # Even the correct password is refused while throttled.
    response = await login(client)

    assert response.status_code == 429
    assert response.headers["retry-after"] == "900"
    assert "set-cookie" not in response.headers
    assert (await audit_events(db_session))[-1].event_type == AuditEventType.LOGIN_BLOCKED

    # Other usernames are unaffected (below the per-IP limit).
    await make_user("manager", role=Roles.GENERAL_MANAGER)
    assert (await login(client, "manager", "manager-password")).status_code == 200


async def test_login_throttled_per_ip_across_usernames(client: AsyncClient) -> None:
    for i in range(20):
        assert (await login(client, username=f"guess{i}")).status_code == 401

    assert (await login(client, username="another")).status_code == 429


async def test_old_failures_outside_window_do_not_throttle(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await make_user("frontdesk")
    for _ in range(5):
        await login(client, password="wrong")
    await db_session.execute(
        update(AuditEvent).values(occurred_at=datetime.now(UTC) - timedelta(minutes=16))
    )

    assert (await login(client)).status_code == 200


async def test_login_replaces_existing_session(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await make_user("frontdesk")
    await login(client)

    await login(client)  # the client sends the first session's cookie

    first, second = await sessions(db_session)
    assert first.revoked_at is not None
    assert second.revoked_at is None


# --- me / protected routes -----------------------------------------------------


async def test_me_returns_current_user(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    user = await make_user("manager", role=Roles.GENERAL_MANAGER)
    await login(client, "manager", "manager-password")

    response = await client.get(ME)

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "id": user.id,
        "username": "manager",
        "full_name": "Manager",
        "property": await property_json(db_session),
        "role": {"id": body["role"]["id"], "name": "General manager"},
        "permissions": permissions_of(Roles.GENERAL_MANAGER),
    }
    assert "settings.manage" not in body["permissions"]


@pytest.mark.parametrize("role", list(USERNAME_FOR_ROLE))
async def test_me_returns_permissions_of_each_seeded_role(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession, role: str
) -> None:
    user = await login_as(role)

    body = (await client.get(ME)).json()

    assert body["id"] == user.id
    assert body["username"] == USERNAME_FOR_ROLE[role]
    assert body["property"] == await property_json(db_session)
    assert body["role"]["name"] == role
    assert body["permissions"] == permissions_of(role)  # sorted, exactly the role's keys


async def test_me_for_user_without_a_role(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("visitor", role=None)
    await login(client, "visitor", "visitor-password")

    body = (await client.get(ME)).json()

    assert (body["role"], body["permissions"]) == (None, [])
    assert (await client.get("/api/dashboard/rooms")).status_code == 403


async def test_protected_route_without_session(client: AsyncClient) -> None:
    response = await client.get(ME)

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


async def test_protected_route_with_unknown_token(
    client: AsyncClient, test_settings: Settings
) -> None:
    client.cookies.set(test_settings.session_cookie_name, "forged-token")

    assert (await client.get(ME)).status_code == 401


async def test_expired_session_rejected(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await make_user("frontdesk")
    await login(client)
    await db_session.execute(
        update(UserSession).values(expires_at=datetime.now(UTC) - timedelta(hours=1))
    )

    assert (await client.get(ME)).status_code == 401


async def test_session_of_deactivated_user_rejected(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    user = await make_user("frontdesk")
    await login(client)

    user.deactivated_at = datetime.now(UTC)
    await db_session.flush()

    assert (await client.get(ME)).status_code == 401


async def test_session_of_user_who_expired_rejected(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    user = await make_user("frontdesk")
    await login(client)

    user.expires_at = datetime.now(UTC) - timedelta(hours=1)
    await db_session.flush()

    assert (await client.get(ME)).status_code == 401


# --- logout --------------------------------------------------------------------


async def test_logout_revokes_session_and_clears_cookie(
    client: AsyncClient, make_user: MakeUser, test_settings: Settings, db_session: AsyncSession
) -> None:
    user = await make_user("frontdesk")
    token = (await login(client)).cookies[test_settings.session_cookie_name]

    response = await client.post(LOGOUT)

    assert response.status_code == 204
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f'{test_settings.session_cookie_name}="";')
    assert "Max-Age=0" in set_cookie
    assert (await client.get(ME)).status_code == 401

    # The server-side session is revoked, so replaying the old cookie fails too.
    client.cookies.set(test_settings.session_cookie_name, token)
    assert (await client.get(ME)).status_code == 401
    [stored] = await sessions(db_session)
    assert stored.revoked_at is not None
    assert (await audit_events(db_session))[-1].event_type == AuditEventType.LOGOUT
    assert (await audit_events(db_session))[-1].user_id == user.id


async def test_logout_without_session_is_noop(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(LOGOUT)

    assert response.status_code == 204
    assert await audit_events(db_session) == []


# --- require_permission --------------------------------------------------------


@pytest.fixture
def user_admin_route(app: FastAPI) -> str:
    router = APIRouter()

    @router.get("/api/test/user-admin")
    async def user_admin(
        user: Annotated[User, Depends(require_permission(P.USER_MANAGE))],
    ) -> dict[str, str]:
        return {"username": user.username}

    app.include_router(router)
    return "/api/test/user-admin"


async def test_require_permission_allows_a_role_that_has_it(
    client: AsyncClient, make_user: MakeUser, user_admin_route: str
) -> None:
    await make_user("manager", role=Roles.GENERAL_MANAGER)
    await login(client, "manager", "manager-password")

    response = await client.get(user_admin_route)

    assert response.status_code == 200
    assert response.json() == {"username": "manager"}


async def test_require_permission_forbids_a_role_without_it(
    client: AsyncClient, make_user: MakeUser, user_admin_route: str
) -> None:
    await make_user("frontdesk", role=Roles.RECEPTIONIST)
    await login(client)

    response = await client.get(user_admin_route)

    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient permissions"}


async def test_require_permission_needs_session(client: AsyncClient, user_admin_route: str) -> None:
    assert (await client.get(user_admin_route)).status_code == 401


def test_require_permission_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="Unknown permission keys"):
        require_permission("rooms.fly")


async def test_permissions_are_loaded_once_per_request(
    app: FastAPI,
    client: AsyncClient,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    real = permission_service.get_effective_permissions

    async def counting(
        db: AsyncSession, user_id: int, property_id: int, *, now: datetime | None = None
    ) -> frozenset[str]:
        calls.append(user_id)
        return await real(db, user_id, property_id, now=now)

    monkeypatch.setattr(permission_service, "get_effective_permissions", counting)
    router = APIRouter()

    @router.get(
        "/api/test/two-checks",
        dependencies=[
            Depends(require_permission(P.DASHBOARD_VIEW)),
            Depends(require_permission(P.RESERVATION_VIEW)),
        ],
    )
    async def two_checks(request: Request) -> dict[str, list[str]]:
        return {"cached": sorted(request.state.permissions)}

    app.include_router(router)
    await make_user("frontdesk", role=Roles.RECEPTIONIST)
    await login(client)  # the login response loads permissions too
    calls.clear()

    response = await client.get("/api/test/two-checks")

    assert response.status_code == 200
    assert response.json()["cached"] == permissions_of(Roles.RECEPTIONIST)
    assert len(calls) == 1
