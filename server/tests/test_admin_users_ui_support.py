"""What the user admin screens rely on: role on create, assignable roles and the
per-user restrictions. (The audit log endpoint is tested in test_audit_log.py.)"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import ALL_PERMISSION_KEYS, DEFAULT_ROLES
from app.models import AuditAction, AuditLog, Role, RolePermission, User
from app.services.permissions import get_default_property_id
from tests.conftest import LoginAs, MakeUser, Roles, role_id

USERS = "/api/admin/users"
ALL_ACTIONS = {
    "edit",
    "change_role",
    "deactivate",
    "reactivate",
    "revoke_sessions",
    "send_invite",
    "reset_password",
}


def blocked(detail: dict[str, Any]) -> dict[str, str]:
    """The restricted actions with their reasons (allowed ones are null)."""
    return {action: reason for action, reason in detail["restrictions"].items() if reason}


# --- create with a role ------------------------------------------------------------------


async def test_create_with_a_role(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)
    receptionist = await role_id(db_session, Roles.RECEPTIONIST)

    response = await client.post(
        USERS, json={"username": "newbie", "full_name": "New Bie", "role_id": receptionist}
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert [r["role"]["name"] for r in data["roles"]] == [Roles.RECEPTIONIST]
    row = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.USER_CREATED)
    )
    assert row is not None and row.after is not None
    assert (row.after["role_id"], row.after["role_name"]) == (receptionist, Roles.RECEPTIONIST)


@pytest.mark.parametrize(("role", "status"), [(Roles.ADMIN, 403), (None, 404)])
async def test_create_with_a_role_you_cannot_assign_creates_nothing(
    client: AsyncClient,
    login_as: LoginAs,
    db_session: AsyncSession,
    role: str | None,
    status: int,
) -> None:
    await login_as(Roles.GENERAL_MANAGER)
    target_role = await role_id(db_session, role) if role else 999999

    response = await client.post(
        USERS, json={"username": "newbie", "full_name": "New Bie", "role_id": target_role}
    )

    assert response.status_code == status
    assert await db_session.scalar(select(User).where(User.username == "newbie")) is None


# --- roles for filters and assignment --------------------------------------------------------


async def test_assignable_roles_follow_your_permissions(
    client: AsyncClient, login_as: LoginAs
) -> None:
    await login_as(Roles.GENERAL_MANAGER)

    roles = (await client.get(f"{USERS}/roles")).json()

    assert [r["name"] for r in roles] == [r.name for r in DEFAULT_ROLES]
    assert {r["name"]: r["assignable"] for r in roles} == {
        Roles.ADMIN: False,  # has settings.manage, which the GM lacks
        Roles.GENERAL_MANAGER: True,
        Roles.FRONT_OFFICE_MANAGER: True,
        Roles.RECEPTIONIST: True,
        Roles.HOUSEKEEPING_SUPERVISOR: True,
        Roles.ROOM_ATTENDANT: True,
    }


async def test_viewers_without_user_manage_can_filter_but_not_assign(
    client: AsyncClient, login_as: LoginAs
) -> None:
    await login_as(Roles.FRONT_OFFICE_MANAGER)  # user.view only

    roles = (await client.get(f"{USERS}/roles")).json()

    assert len(roles) == len(DEFAULT_ROLES)
    assert not any(r["assignable"] for r in roles)


# --- restrictions ------------------------------------------------------------------------


async def test_restrictions_on_yourself(client: AsyncClient, login_as: LoginAs) -> None:
    me = await login_as(Roles.ADMIN)

    restrictions = blocked((await client.get(f"{USERS}/{me.id}")).json())

    assert restrictions == {
        "change_role": "You cannot change your own role.",
        "deactivate": "You cannot deactivate yourself.",
        "reactivate": "admin is not deactivated.",
        "send_invite": "admin has already activated their account.",
    }


async def test_restrictions_for_an_invited_user(
    client: AsyncClient, login_as: LoginAs, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)
    newbie = await make_user("newbie")
    newbie.password_hash = None
    await db_session.flush()

    restrictions = blocked((await client.get(f"{USERS}/{newbie.id}")).json())

    assert set(restrictions) == {"reactivate", "reset_password"}
    assert restrictions["reset_password"] == "newbie has not activated their account yet."


async def test_restrictions_for_a_deactivated_user(
    client: AsyncClient, login_as: LoginAs, make_user: MakeUser
) -> None:
    await login_as(Roles.ADMIN)
    gone = await make_user("gone", deactivated=True)

    restrictions = blocked((await client.get(f"{USERS}/{gone.id}")).json())

    assert set(restrictions) == {"deactivate", "send_invite", "reset_password"}
    assert restrictions["reset_password"] == "gone is deactivated; links cannot be sent."


async def test_restrictions_for_an_expired_user(
    client: AsyncClient, login_as: LoginAs, make_user: MakeUser
) -> None:
    await login_as(Roles.ADMIN)
    old = await make_user("old", expires_at=datetime.now(UTC) - timedelta(days=1))

    restrictions = blocked((await client.get(f"{USERS}/{old.id}")).json())

    assert restrictions["reset_password"] == "old is expired; links cannot be sent."
    assert "deactivate" not in restrictions


async def test_everything_restricted_for_a_more_powerful_user(
    client: AsyncClient, login_as: LoginAs, make_user: MakeUser
) -> None:
    await login_as(Roles.GENERAL_MANAGER)
    boss = await make_user("boss", role=Roles.ADMIN)

    restrictions = blocked((await client.get(f"{USERS}/{boss.id}")).json())

    assert restrictions == dict.fromkeys(ALL_ACTIONS, "boss has permissions you don't have.")


async def test_everything_restricted_without_user_manage(
    client: AsyncClient, login_as: LoginAs, make_user: MakeUser
) -> None:
    await login_as(Roles.FRONT_OFFICE_MANAGER)
    desk = await make_user("desk", role=Roles.RECEPTIONIST)

    restrictions = blocked((await client.get(f"{USERS}/{desk.id}")).json())

    assert restrictions == dict.fromkeys(ALL_ACTIONS, "You do not have permission to manage users.")


async def test_last_admin_restriction(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    # Someone with every permission through a custom role (not the Admin role) looks
    # at the property's only active Admin.
    deputy_role = Role(
        property_id=await get_default_property_id(db_session), name="Deputy", is_system=False
    )
    db_session.add(deputy_role)
    await db_session.flush()
    db_session.add_all(
        RolePermission(role_id=deputy_role.id, permission_key=k) for k in ALL_PERMISSION_KEYS
    )
    await make_user("deputy", role="Deputy")
    await client.post("/api/auth/login", json={"username": "deputy", "password": "deputy-password"})
    boss = await make_user("boss", role=Roles.ADMIN)

    restrictions = blocked((await client.get(f"{USERS}/{boss.id}")).json())

    reason = "boss is the last active Admin. Give another user the Admin role first."
    assert (restrictions["change_role"], restrictions["deactivate"]) == (reason, reason)
    assert "edit" not in restrictions and "revoke_sessions" not in restrictions
