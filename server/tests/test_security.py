"""Cross-cutting security checks: route access, origin check, headers, settings."""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.permissions import ALL_PERMISSION_KEYS, DEFAULT_ROLES, P
from app.main import create_app
from tests.conftest import USERNAME_FOR_ROLE, LoginAs

PUBLIC = "public"
SIGNED_IN = "any signed-in user"

# What each REST operation requires: a permission key, any signed-in user, or nothing.
# Every operation in the OpenAPI schema must be listed: adding an endpoint fails this
# test until its access is decided here.
ACCESS: dict[tuple[str, str], str] = {
    ("GET", "/api/health"): PUBLIC,
    ("POST", "/api/auth/login"): PUBLIC,
    ("POST", "/api/auth/logout"): PUBLIC,
    ("GET", "/api/auth/me"): SIGNED_IN,
    ("GET", "/api/hotel"): SIGNED_IN,
    ("GET", "/api/dashboard/rooms"): P.DASHBOARD_VIEW,
    ("GET", "/api/dashboard/summary"): P.DASHBOARD_VIEW,
    ("GET", "/api/reservations/{reservation_id}"): P.RESERVATION_VIEW,
    ("POST", "/api/reservations/{reservation_id}/check-in"): P.RESERVATION_CHECK_IN,
    ("POST", "/api/reservations/{reservation_id}/check-out"): P.RESERVATION_CHECK_OUT,
    ("PATCH", "/api/rooms/{room_id}/housekeeping-status"): P.ROOM_UPDATE_HOUSEKEEPING_STATUS,
    ("GET", "/api/admin/users"): P.USER_VIEW,
    ("GET", "/api/admin/users/{user_id}"): P.USER_VIEW,
    ("POST", "/api/admin/users"): P.USER_MANAGE,
    ("PATCH", "/api/admin/users/{user_id}"): P.USER_MANAGE,
    ("PUT", "/api/admin/users/{user_id}/role"): P.USER_MANAGE,
    ("POST", "/api/admin/users/{user_id}/deactivate"): P.USER_MANAGE,
    ("POST", "/api/admin/users/{user_id}/reactivate"): P.USER_MANAGE,
    ("POST", "/api/admin/users/{user_id}/revoke-sessions"): P.USER_MANAGE,
    ("POST", "/api/admin/users/{user_id}/invite"): P.USER_MANAGE,
    ("POST", "/api/admin/users/{user_id}/reset-password"): P.USER_MANAGE,
    ("GET", "/api/admin/users/roles"): P.USER_VIEW,
    ("GET", "/api/admin/audit"): P.AUDIT_VIEW,
    ("GET", "/api/admin/room-types"): P.ROOM_MANAGE,
    ("POST", "/api/admin/room-types"): P.ROOM_MANAGE,
    ("PATCH", "/api/admin/room-types/{room_type_id}"): P.ROOM_MANAGE,
    ("POST", "/api/admin/room-types/{room_type_id}/retire"): P.ROOM_MANAGE,
    ("POST", "/api/admin/room-types/{room_type_id}/reactivate"): P.ROOM_MANAGE,
    ("GET", "/api/admin/rooms"): P.ROOM_MANAGE,
    ("POST", "/api/admin/rooms"): P.ROOM_MANAGE,
    ("PATCH", "/api/admin/rooms/{room_id}"): P.ROOM_MANAGE,
    ("POST", "/api/admin/rooms/{room_id}/retire"): P.ROOM_MANAGE,
    ("POST", "/api/admin/rooms/{room_id}/reactivate"): P.ROOM_MANAGE,
    ("GET", "/api/admin/roles"): P.ROLE_VIEW,
    ("GET", "/api/admin/roles/permissions"): P.ROLE_VIEW,
    ("GET", "/api/admin/roles/{role_id}"): P.ROLE_VIEW,
    ("POST", "/api/admin/roles"): P.ROLE_MANAGE,
    ("PATCH", "/api/admin/roles/{role_id}"): P.ROLE_MANAGE,
    ("DELETE", "/api/admin/roles/{role_id}"): P.ROLE_MANAGE,
    ("POST", "/api/auth/activate/check"): PUBLIC,
    ("POST", "/api/auth/activate"): PUBLIC,
    ("POST", "/api/auth/reset-password/check"): PUBLIC,
    ("POST", "/api/auth/reset-password"): PUBLIC,
    ("POST", "/api/auth/forgot-password"): PUBLIC,
}
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {r.name: r.permissions for r in DEFAULT_ROLES}


def test_access_rules_use_known_permissions() -> None:
    rules = set(ACCESS.values()) - {PUBLIC, SIGNED_IN}
    assert rules <= ALL_PERMISSION_KEYS


def operations(app: FastAPI) -> set[tuple[str, str]]:
    schema: dict[str, Any] = app.openapi()
    return {
        (method.upper(), path) for path, methods in schema["paths"].items() for method in methods
    }


def concrete(path: str) -> str:
    # A user id no real user has, so allowed requests can't change anyone (404).
    return (
        path.replace("{reservation_id}", "1")
        .replace("{room_id}", "1")
        .replace("{user_id}", "999999")
        .replace("{role_id}", "999999")
        .replace("{room_type_id}", "999999")
        .replace("{token}", "x" * 43)
    )


def test_every_operation_has_a_declared_access_rule(app: FastAPI) -> None:
    assert operations(app) == set(ACCESS)


@pytest.mark.parametrize(("method", "path"), [op for op, rule in ACCESS.items() if rule != PUBLIC])
async def test_protected_operations_require_a_session(
    client: AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, concrete(path), json={})

    assert response.status_code == 401


@pytest.mark.parametrize("role", list(USERNAME_FOR_ROLE))
async def test_each_default_role_reaches_exactly_what_its_permissions_allow(
    client: AsyncClient, login_as: LoginAs, role: str
) -> None:
    await login_as(role)

    for (method, path), rule in ACCESS.items():
        if rule == PUBLIC:
            continue
        response = await client.request(method, concrete(path), json={})
        if rule == SIGNED_IN or rule in ROLE_PERMISSIONS[role]:
            # Allowed: may still be 404/409/422 for this dummy request, never 401/403.
            assert response.status_code not in (401, 403), (method, path, role)
        else:
            assert response.status_code == 403, (method, path, role)


# --- origin check (CSRF defence in depth) ------------------------------------------


async def test_state_changing_request_from_foreign_origin_is_refused(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/auth/login",
        json={"username": "frontdesk", "password": "x"},
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Origin not allowed", "code": "origin_not_allowed"}


async def test_allowed_origin_and_same_origin_pass(
    client: AsyncClient, test_settings: Settings
) -> None:
    for origin in (test_settings.cors_origins[0], "https://test"):
        response = await client.post(
            "/api/auth/login",
            json={"username": "nobody", "password": "x"},
            headers={"Origin": origin},
        )
        assert response.status_code == 401, origin  # reached the endpoint


async def test_requests_without_origin_pass(client: AsyncClient) -> None:
    response = await client.post("/api/auth/login", json={"username": "nobody", "password": "x"})

    assert response.status_code == 401


async def test_safe_methods_are_not_origin_checked(client: AsyncClient) -> None:
    response = await client.get("/api/health", headers={"Origin": "https://evil.example"})

    assert response.status_code == 200
    # ...but CORS still withholds the response from that origin's scripts.
    assert "access-control-allow-origin" not in response.headers


# --- headers -----------------------------------------------------------------------


async def test_security_headers(client: AsyncClient) -> None:
    response = await client.get("/api/health")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"


# --- settings ----------------------------------------------------------------------

STRONG_SECRET = "s" * 40


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "database_url": "postgresql+asyncpg://u:p@db/x",
        "session_secret": STRONG_SECRET,
        "session_cookie_secure": True,
        "cors_origins": "https://hotel.example",
        "email_backend": "disabled",
        **overrides,
    }
    return Settings.model_validate(values)


def test_production_settings_accept_safe_values() -> None:
    assert production_settings().environment == "production"


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"session_cookie_secure": False}, "SESSION_COOKIE_SECURE must be true"),
        ({"session_secret": "change-me"}, "SESSION_SECRET must be a random value"),
        ({"session_secret": "short"}, "SESSION_SECRET must be a random value"),
        ({"email_backend": "console"}, "EMAIL_BACKEND must be a real email service"),
    ],
)
def test_production_refuses_unsafe_settings(overrides: dict[str, Any], problem: str) -> None:
    with pytest.raises(ValidationError, match=problem):
        production_settings(**overrides)


def test_wildcard_cors_origin_rejected_in_any_environment() -> None:
    with pytest.raises(ValidationError, match="not"):
        production_settings(environment="development", cors_origins="*")


async def test_api_docs_hidden_in_production() -> None:
    app = create_app(production_settings())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await client.get(path)).status_code == 404, path
