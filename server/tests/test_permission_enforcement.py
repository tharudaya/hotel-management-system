"""Every permission-protected endpoint, with and without its permission.

For each operation in ACCESS (tests/test_security.py) that needs a permission, two
users with custom roles that differ by exactly that key: one has only that key, the
other has every key except it. So a pass or a 403 can only come from that permission.
Operations that need several keys get one user with exactly those keys, and one user
per key without it.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import ALL_PERMISSION_KEYS
from app.models import Role, RolePermission, UserPropertyRole
from app.services.permissions import get_default_property_id
from tests.conftest import MakeUser
from tests.test_security import ACCESS, PUBLIC, SIGNED_IN, concrete, required_keys

PROTECTED: list[tuple[str, str, frozenset[str]]] = [
    (method, path, required_keys(rule))
    for (method, path), rule in ACCESS.items()
    if rule not in (PUBLIC, SIGNED_IN)
]
# Each required key on its own: missing it must be enough for a 403.
EACH_KEY: list[tuple[str, str, str]] = [
    (method, path, key) for method, path, keys in PROTECTED for key in sorted(keys)
]


async def login_with_permissions(
    client: AsyncClient, db: AsyncSession, make_user: MakeUser, keys: frozenset[str]
) -> None:
    """A user whose only role holds exactly `keys`, logged in."""
    property_id = await get_default_property_id(db)
    role = Role(property_id=property_id, name="Test role", is_system=False)
    db.add(role)
    await db.flush()
    db.add_all(RolePermission(role_id=role.id, permission_key=key) for key in keys)
    user = await make_user("tester", role=None)
    db.add(UserPropertyRole(user_id=user.id, property_id=property_id, role_id=role.id))
    await db.flush()
    response = await client.post(
        "/api/auth/login", json={"username": "tester", "password": "tester-password"}
    )
    assert response.status_code == 200, response.text


def test_every_protected_operation_is_covered() -> None:
    assert PROTECTED, "no protected operations found"
    assert {key for _, _, key in EACH_KEY} <= ALL_PERMISSION_KEYS


@pytest.mark.parametrize(("method", "path", "permissions"), PROTECTED)
async def test_allowed_with_the_permission(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    method: str,
    path: str,
    permissions: frozenset[str],
) -> None:
    await login_with_permissions(client, db_session, make_user, permissions)

    response = await client.request(method, concrete(path), json={})

    # May still be 404/409/422 for this dummy request, but never refused.
    assert response.status_code not in (401, 403), response.text


@pytest.mark.parametrize(("method", "path", "permission"), EACH_KEY)
async def test_forbidden_without_the_permission(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    method: str,
    path: str,
    permission: str,
) -> None:
    await login_with_permissions(client, db_session, make_user, ALL_PERMISSION_KEYS - {permission})

    response = await client.request(method, concrete(path), json={})

    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient permissions"}
