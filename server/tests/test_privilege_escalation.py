"""Attempts to gain more power than you have. Every one of them must fail and change
nothing. Grouped by attack: granting yourself more, assigning Admin from below,
touching the Admin role, removing the last admin, and reaching into another property.
"""

from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import ALL_PERMISSION_KEYS, DEFAULT_ROLES, P
from app.models import Property, Role, RolePermission, User, UserPropertyRole
from app.services.permissions import get_default_property_id, get_effective_permissions
from tests.conftest import LoginAs, MakeUser, Roles, role_id

USERS = "/api/admin/users"
ROLES = "/api/admin/roles"
FOM_PERMISSIONS = next(r.permissions for r in DEFAULT_ROLES if r.name == Roles.FRONT_OFFICE_MANAGER)


def forbidden(response: Response) -> None:
    """Refused by a permission guard specifically."""
    assert response.status_code == 403, (response.status_code, response.text)


def refused(response: Response) -> None:
    """Refused for a reason (403/404/409/422), never a 2xx or a server error."""
    assert response.status_code in (403, 404, 409, 422), (response.status_code, response.text)


async def custom_role(
    db: AsyncSession, name: str, keys: frozenset[str], property_id: int | None = None
) -> Role:
    role = Role(
        property_id=property_id or await get_default_property_id(db), name=name, is_system=False
    )
    db.add(role)
    await db.flush()
    db.add_all(RolePermission(role_id=role.id, permission_key=k) for k in keys)
    await db.flush()
    return role


async def login_with_role(
    client: AsyncClient, make_user: MakeUser, username: str, role: str
) -> User:
    user = await make_user(username, role=role)
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": f"{username}-password"}
    )
    assert response.status_code == 200, response.text
    return user


async def permissions_of(db: AsyncSession, user: User) -> frozenset[str]:
    return await get_effective_permissions(db, user.id, await get_default_property_id(db))


# --- granting yourself more ---------------------------------------------------------------


async def test_cannot_add_a_permission_to_your_own_role(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    gm = await login_as(Roles.GENERAL_MANAGER)
    gm_role = await role_id(db_session, Roles.GENERAL_MANAGER)

    response = await client.patch(
        f"{ROLES}/{gm_role}", json={"permissions": sorted(ALL_PERMISSION_KEYS)}
    )

    assert response.status_code == 403
    assert response.json()["code"] == "permission_not_held"
    assert P.SETTINGS_MANAGE not in await permissions_of(db_session, gm)


async def test_cannot_give_yourself_another_role(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    gm = await login_as(Roles.GENERAL_MANAGER)

    for role in (Roles.ADMIN, Roles.RECEPTIONIST):
        refused(
            await client.put(
                f"{USERS}/{gm.id}/role", json={"role_id": await role_id(db_session, role)}
            )
        )
    assert P.SETTINGS_MANAGE not in await permissions_of(db_session, gm)


async def test_cannot_create_or_copy_a_role_stronger_than_you(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.GENERAL_MANAGER)

    forbidden(await client.post(ROLES, json={"name": "Root", "permissions": [P.SETTINGS_MANAGE]}))
    forbidden(
        await client.post(
            ROLES,
            json={"name": "Root", "copy_from_role_id": await role_id(db_session, Roles.ADMIN)},
        )
    )
    assert await db_session.scalar(select(Role).where(Role.name == "Root")) is None


async def test_role_manager_cannot_add_user_manage_to_their_own_role(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    keys = frozenset({P.ROLE_VIEW, P.ROLE_MANAGE, P.DASHBOARD_VIEW})
    role = await custom_role(db_session, "Role keeper", keys)
    await login_with_role(client, make_user, "keeper", "Role keeper")

    response = await client.patch(
        f"{ROLES}/{role.id}", json={"permissions": sorted(keys | {P.USER_MANAGE})}
    )

    assert response.status_code == 403


async def test_user_manager_cannot_hand_out_or_touch_stronger_access(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    """A narrow "user manager" (users only) vs. a Receptionist, who has more."""
    await custom_role(db_session, "User clerk", frozenset({P.USER_VIEW, P.USER_MANAGE}))
    await login_with_role(client, make_user, "clerk", "User clerk")
    receptionist = await role_id(db_session, Roles.RECEPTIONIST)
    desk = await make_user("desk", role=Roles.RECEPTIONIST)
    newbie = await make_user("newbie", role=None)

    forbidden(
        await client.post(
            USERS, json={"username": "intruder1", "full_name": "X", "role_id": receptionist}
        )
    )
    forbidden(await client.put(f"{USERS}/{newbie.id}/role", json={"role_id": receptionist}))
    for action in ("deactivate", "revoke-sessions", "reset-password"):
        forbidden(await client.post(f"{USERS}/{desk.id}/{action}"))
    forbidden(await client.patch(f"{USERS}/{desk.id}", json={"email": "attacker@example.com"}))

    await db_session.refresh(desk)
    assert (desk.deactivated_at, desk.email) == (None, None)
    assert await db_session.scalar(select(User).where(User.username == "intruder1")) is None


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "POST",
            USERS,
            {"username": "intruder2", "full_name": "X", "password_hash": "$argon2id$..."},
        ),
        ("POST", USERS, {"username": "intruder2", "full_name": "X", "is_admin": True}),
        ("PATCH", "{self}", {"roles": [{"role": {"id": 1}}]}),
        ("PATCH", "{self}", {"deactivated_at": None, "username": "admin2"}),
        ("POST", ROLES, {"name": "Sneaky", "is_system": True}),
    ],
)
async def test_extra_fields_cannot_smuggle_in_privileges(
    client: AsyncClient,
    login_as: LoginAs,
    method: str,
    path: str,
    body: dict[str, Any],
) -> None:
    me = await login_as(Roles.GENERAL_MANAGER)

    response = await client.request(method, path.replace("{self}", f"{USERS}/{me.id}"), json=body)

    assert response.status_code == 422


# --- assigning Admin from below ----------------------------------------------------------


async def test_front_office_manager_cannot_assign_admin(
    client: AsyncClient, login_as: LoginAs, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await login_as(Roles.FRONT_OFFICE_MANAGER)
    desk = await make_user("desk", role=Roles.RECEPTIONIST)
    admin = await role_id(db_session, Roles.ADMIN)

    assert (await client.put(f"{USERS}/{desk.id}/role", json={"role_id": admin})).status_code == 403
    assert (
        await client.post(USERS, json={"username": "intruder3", "full_name": "X", "role_id": admin})
    ).status_code == 403
    assert P.SETTINGS_MANAGE not in await permissions_of(db_session, desk)


async def test_front_office_manager_with_user_manage_still_cannot_assign_admin(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    # Even with user.manage added, Admin grants far more than they hold.
    await custom_role(db_session, "FOM plus", FOM_PERMISSIONS | {P.USER_MANAGE})
    await login_with_role(client, make_user, "fomplus", "FOM plus")
    desk = await make_user("desk", role=Roles.RECEPTIONIST)
    for role in (Roles.ADMIN, Roles.GENERAL_MANAGER):
        response = await client.put(
            f"{USERS}/{desk.id}/role", json={"role_id": await role_id(db_session, role)}
        )
        assert response.status_code == 403, role
    # A role within their own powers is fine (the guard is not just "deny all").
    ok = await client.put(
        f"{USERS}/{desk.id}/role",
        json={"role_id": await role_id(db_session, Roles.HOUSEKEEPING_SUPERVISOR)},
    )
    assert ok.status_code == 200


# --- the Admin role ------------------------------------------------------------------------


@pytest.mark.parametrize("who", [Roles.ADMIN, Roles.GENERAL_MANAGER])
@pytest.mark.parametrize(
    "body",
    [
        {"permissions": [P.DASHBOARD_VIEW]},
        {"permissions": []},
        {"name": "Superuser"},
        {"description": "pwned"},
    ],
)
async def test_nobody_can_edit_the_admin_role(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession, who: str, body: dict[str, Any]
) -> None:
    await login_as(who)
    admin = await role_id(db_session, Roles.ADMIN)

    refused(await client.patch(f"{ROLES}/{admin}", json=body))
    refused(await client.delete(f"{ROLES}/{admin}"))

    role = await db_session.get(Role, admin)
    assert role is not None
    await db_session.refresh(role)
    assert (role.name, role.is_system) == (Roles.ADMIN, True)


# --- the last admin -----------------------------------------------------------------------


async def test_the_last_admin_cannot_be_removed_by_anyone(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    # A "deputy" holds every permission through a custom role, so they may manage the
    # Admin; only the last-admin rule stands in the way.
    await custom_role(db_session, "Deputy", ALL_PERMISSION_KEYS)
    await login_with_role(client, make_user, "deputy", "Deputy")
    boss = await make_user("boss", role=Roles.ADMIN)
    receptionist = await role_id(db_session, Roles.RECEPTIONIST)

    attempts = [
        await client.post(f"{USERS}/{boss.id}/deactivate"),
        await client.put(f"{USERS}/{boss.id}/role", json={"role_id": receptionist}),
        await client.patch(f"{USERS}/{boss.id}", json={"expires_at": "2030-01-01T00:00:00Z"}),
    ]

    for response in attempts:
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "last_admin"
    await db_session.refresh(boss)
    assert boss.deactivated_at is None and boss.expires_at is None


async def test_an_admin_cannot_remove_themselves(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    me = await login_as(Roles.ADMIN)
    receptionist = await role_id(db_session, Roles.RECEPTIONIST)

    for response in (
        await client.post(f"{USERS}/{me.id}/deactivate"),
        await client.put(f"{USERS}/{me.id}/role", json={"role_id": receptionist}),
        await client.patch(f"{USERS}/{me.id}", json={"expires_at": "2020-01-01T00:00:00Z"}),
    ):
        refused(response)
    assert P.SETTINGS_MANAGE in await permissions_of(db_session, me)


# --- another property's ids ------------------------------------------------------------------


async def other_property(db: AsyncSession) -> tuple[Property, Role]:
    other = Property(name="Other Hotel", time_zone="UTC")
    db.add(other)
    await db.flush()
    admin = await custom_role(db, "Other admin", ALL_PERMISSION_KEYS, property_id=other.id)
    return other, admin


async def test_roles_of_another_property_are_out_of_reach(
    client: AsyncClient, login_as: LoginAs, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)
    _, foreign = await other_property(db_session)
    desk = await make_user("desk", role=Roles.RECEPTIONIST)

    for response in (
        await client.get(f"{ROLES}/{foreign.id}"),
        await client.patch(f"{ROLES}/{foreign.id}", json={"permissions": []}),
        await client.delete(f"{ROLES}/{foreign.id}"),
        await client.post(ROLES, json={"name": "Copy", "copy_from_role_id": foreign.id}),
        await client.put(f"{USERS}/{desk.id}/role", json={"role_id": foreign.id}),
        await client.post(
            USERS, json={"username": "intruder4", "full_name": "X", "role_id": foreign.id}
        ),
    ):
        assert response.status_code == 404, response.text
    assert await db_session.scalar(select(User).where(User.username == "intruder4")) is None


async def test_cannot_manage_another_propertys_admin(
    client: AsyncClient, login_as: LoginAs, make_user: MakeUser, db_session: AsyncSession
) -> None:
    """Users are global. An Admin here holds nothing at the other property, so its admin
    (who has no role here at all) must still be out of reach."""
    await login_as(Roles.ADMIN)
    other, other_admin_role = await other_property(db_session)
    theirs = await make_user("theirs", role=None)
    db_session.add(
        UserPropertyRole(user_id=theirs.id, property_id=other.id, role_id=other_admin_role.id)
    )
    await db_session.flush()

    detail = (await client.get(f"{USERS}/{theirs.id}")).json()
    assert {v for v in detail["restrictions"].values()} == {
        "theirs has permissions you don't have."
    }
    for response in (
        await client.post(f"{USERS}/{theirs.id}/deactivate"),
        await client.post(f"{USERS}/{theirs.id}/reset-password"),
        await client.post(f"{USERS}/{theirs.id}/revoke-sessions"),
        await client.patch(f"{USERS}/{theirs.id}", json={"email": "attacker@example.com"}),
        await client.put(
            f"{USERS}/{theirs.id}/role",
            json={"role_id": await role_id(db_session, Roles.RECEPTIONIST)},
        ),
    ):
        assert response.status_code == 403, response.text
    await db_session.refresh(theirs)
    assert (theirs.deactivated_at, theirs.email) == (None, None)


async def test_an_admin_of_both_properties_may_manage_them(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    other, other_admin_role = await other_property(db_session)
    me = await login_with_role(client, make_user, "both", Roles.ADMIN)
    db_session.add(
        UserPropertyRole(user_id=me.id, property_id=other.id, role_id=other_admin_role.id)
    )
    theirs = await make_user("theirs", role=None)
    db_session.add(
        UserPropertyRole(user_id=theirs.id, property_id=other.id, role_id=other_admin_role.id)
    )
    await db_session.flush()

    response = await client.patch(f"{USERS}/{theirs.id}", json={"job_title": "Night manager"})

    assert response.status_code == 200, response.text
