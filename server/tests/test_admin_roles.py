"""Role administration API: every endpoint and every rule."""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import ALL_PERMISSION_KEYS, DEFAULT_ROLES, PERMISSIONS, P
from app.models import AuditAction, AuditLog, Property, Role, RolePermission
from app.realtime.events import DomainEvent, PermissionsChanged
from app.services.permissions import get_default_property_id
from tests.conftest import LoginAs, MakeUser, Roles, role_id

ROLES = "/api/admin/roles"
GM_PERMISSIONS = ALL_PERMISSION_KEYS - {P.SETTINGS_MANAGE}


# --- helpers ---------------------------------------------------------------------------


@pytest.fixture
def published(app: FastAPI) -> list[DomainEvent]:
    events: list[DomainEvent] = []

    async def collect(event: DomainEvent) -> None:
        events.append(event)

    app.state.event_bus.subscribe(collect)
    return events


async def custom_role(
    db: AsyncSession, name: str, keys: set[str], description: str | None = None
) -> Role:
    role = Role(
        property_id=await get_default_property_id(db),
        name=name,
        description=description,
        is_system=False,
    )
    db.add(role)
    await db.flush()
    db.add_all(RolePermission(role_id=role.id, permission_key=k) for k in keys)
    await db.flush()
    return role


async def stored_keys(db: AsyncSession, role_id_: int) -> set[str]:
    keys = await db.scalars(
        select(RolePermission.permission_key).where(RolePermission.role_id == role_id_)
    )
    return set(keys.all())


async def role_audit(db: AsyncSession) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog).where(AuditLog.target_type == "role").order_by(AuditLog.id)
    )
    return list(rows.all())


def by_name(roles: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {role["name"]: role for role in roles}


# --- reads -----------------------------------------------------------------------------


async def test_list_roles_with_counts(
    client: AsyncClient, login_as: LoginAs, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)
    await make_user("desk1", role=Roles.RECEPTIONIST)
    await make_user("desk2", role=Roles.RECEPTIONIST, deactivated=True)
    await custom_role(db_session, "Night auditor", {P.DASHBOARD_VIEW, P.REPORT_VIEW})

    response = await client.get(ROLES)

    assert response.status_code == 200
    roles = response.json()
    assert [r["name"] for r in roles] == [r.name for r in DEFAULT_ROLES] + ["Night auditor"]
    named = by_name(roles)
    admin = named[Roles.ADMIN]
    assert (admin["is_system"], admin["is_admin"], admin["can_edit"], admin["can_delete"]) == (
        True,
        True,
        False,
        False,
    )
    assert admin["can_duplicate"] is True  # an Admin may copy it, never edit it
    assert admin["permission_count"] == len(ALL_PERMISSION_KEYS)
    assert admin["user_count"] == 1
    receptionist = named[Roles.RECEPTIONIST]
    assert receptionist["user_count"] == 2  # deactivated holders still hold it
    assert receptionist["permission_count"] == len(DEFAULT_ROLES[3].permissions)
    assert (receptionist["can_edit"], receptionist["can_delete"]) == (True, False)
    night = named["Night auditor"]
    assert night == {
        "id": night["id"],
        "name": "Night auditor",
        "description": None,
        "is_system": False,
        "is_admin": False,
        "user_count": 0,
        "permission_count": 2,
        "can_edit": True,
        "can_delete": True,
        "can_duplicate": True,
    }


async def test_list_hints_follow_the_viewers_own_permissions(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.GENERAL_MANAGER)
    await custom_role(db_session, "Settings clerk", {P.SETTINGS_MANAGE})

    named = by_name((await client.get(ROLES)).json())

    assert named[Roles.GENERAL_MANAGER]["can_edit"] is True
    assert named["Settings clerk"]["can_edit"] is False  # has a key the GM lacks
    assert named["Settings clerk"]["can_delete"] is False
    assert named["Settings clerk"]["can_duplicate"] is False
    assert named[Roles.ADMIN]["can_duplicate"] is False
    assert named[Roles.RECEPTIONIST]["can_duplicate"] is True


async def test_permission_catalog_grouped_with_descriptions(
    client: AsyncClient, login_as: LoginAs
) -> None:
    await login_as(Roles.GENERAL_MANAGER)

    groups = (await client.get(f"{ROLES}/permissions")).json()

    assert [g["group"] for g in groups] == list(dict.fromkeys(p.group for p in PERMISSIONS))
    flat = [(item["key"], item["description"]) for g in groups for item in g["permissions"]]
    assert flat == [(p.key, p.description) for p in PERMISSIONS]
    rooms = next(g for g in groups if g["group"] == "rooms")
    assert {
        "key": P.ROOM_SET_OUT_OF_ORDER,
        "description": "Take rooms out of order or back into service",
    } in rooms["permissions"]


async def test_role_detail_has_keys_and_users(
    client: AsyncClient, login_as: LoginAs, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)
    bea = await make_user("bea", role=Roles.HOUSEKEEPING_SUPERVISOR)
    bea.full_name = "Bea Clean"
    gone = await make_user("al", role=Roles.HOUSEKEEPING_SUPERVISOR, deactivated=True)
    gone.full_name = "Al Away"
    await db_session.flush()
    supervisor_id = await role_id(db_session, Roles.HOUSEKEEPING_SUPERVISOR)

    response = await client.get(f"{ROLES}/{supervisor_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == Roles.HOUSEKEEPING_SUPERVISOR
    assert data["permissions"] == sorted(DEFAULT_ROLES[4].permissions)
    assert data["users"] == [
        {"id": gone.id, "username": "al", "full_name": "Al Away", "status": "deactivated"},
        {"id": bea.id, "username": "bea", "full_name": "Bea Clean", "status": "active"},
    ]
    assert data["user_count"] == 2


async def test_admin_role_detail_lists_the_whole_catalog(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)

    data = (await client.get(f"{ROLES}/{await role_id(db_session, Roles.ADMIN)}")).json()

    assert data["permissions"] == sorted(ALL_PERMISSION_KEYS)
    assert data["is_admin"] is True


async def test_role_at_another_property_is_not_found(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)
    other = Property(name="Other Hotel", time_zone="UTC")
    db_session.add(other)
    await db_session.flush()
    foreign = Role(property_id=other.id, name="Elsewhere", is_system=False)
    db_session.add(foreign)
    await db_session.flush()

    for response in (
        await client.get(f"{ROLES}/{foreign.id}"),
        await client.patch(f"{ROLES}/{foreign.id}", json={"name": "Mine now"}),
        await client.delete(f"{ROLES}/{foreign.id}"),
        await client.post(ROLES, json={"name": "Copy", "copy_from_role_id": foreign.id}),
    ):
        assert response.status_code == 404


async def test_reads_need_role_view(client: AsyncClient, login_as: LoginAs) -> None:
    await login_as(Roles.FRONT_OFFICE_MANAGER)  # has user.view, not role.view

    for path in (ROLES, f"{ROLES}/permissions", f"{ROLES}/1"):
        assert (await client.get(path)).status_code == 403


async def test_reads_need_a_session(client: AsyncClient) -> None:
    assert (await client.get(ROLES)).status_code == 401
    assert (await client.post(ROLES, json={"name": "x"})).status_code == 401


# --- create -----------------------------------------------------------------------------


async def test_create_role(
    client: AsyncClient,
    login_as: LoginAs,
    db_session: AsyncSession,
    published: list[DomainEvent],
) -> None:
    actor = await login_as(Roles.GENERAL_MANAGER)

    response = await client.post(
        ROLES,
        json={
            "name": "  Night auditor ",
            "description": "Overnight reports",
            "permissions": [P.REPORT_VIEW, P.DASHBOARD_VIEW, P.REPORT_VIEW],
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert (data["name"], data["description"], data["is_system"]) == (
        "Night auditor",
        "Overnight reports",
        False,
    )
    assert data["permissions"] == [P.DASHBOARD_VIEW, P.REPORT_VIEW]
    assert data["users"] == []
    assert await stored_keys(db_session, data["id"]) == {P.DASHBOARD_VIEW, P.REPORT_VIEW}
    [row] = await role_audit(db_session)
    assert (row.action, row.actor_user_id, row.target_id, row.before) == (
        AuditAction.ROLE_CREATED,
        actor.id,
        str(data["id"]),
        None,
    )
    assert row.after == {
        "name": "Night auditor",
        "description": "Overnight reports",
        "permissions": {"added": [P.DASHBOARD_VIEW, P.REPORT_VIEW], "removed": []},
    }
    assert published == []  # nobody holds it yet


async def test_create_by_copying_another_role(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)
    source = await role_id(db_session, Roles.RECEPTIONIST)

    response = await client.post(
        ROLES,
        json={
            "name": "Senior receptionist",
            "copy_from_role_id": source,
            "permissions": [P.REPORT_VIEW],
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert set(data["permissions"]) == DEFAULT_ROLES[3].permissions | {P.REPORT_VIEW}
    assert data["description"] == "Day-to-day front desk work"  # copied when not given
    [row] = await role_audit(db_session)
    assert row.after is not None
    assert row.after["copied_from"] == {"id": source, "name": Roles.RECEPTIONIST}


async def test_manager_cannot_create_a_role_with_settings_manage(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.GENERAL_MANAGER)  # role.manage, but not settings.manage

    response = await client.post(
        ROLES, json={"name": "Settings clerk", "permissions": [P.DASHBOARD_VIEW, P.SETTINGS_MANAGE]}
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "You cannot grant permissions you don't have: settings.manage.",
        "code": "permission_not_held",
    }
    assert await db_session.scalar(select(Role).where(Role.name == "Settings clerk")) is None
    assert await role_audit(db_session) == []


async def test_manager_cannot_copy_the_admin_role(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.GENERAL_MANAGER)

    response = await client.post(
        ROLES,
        json={"name": "Almost admin", "copy_from_role_id": await role_id(db_session, Roles.ADMIN)},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "permission_not_held"


async def test_admin_can_copy_admin_into_a_normal_custom_role(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)

    data = (
        await client.post(
            ROLES,
            json={"name": "Deputy", "copy_from_role_id": await role_id(db_session, Roles.ADMIN)},
        )
    ).json()

    assert set(data["permissions"]) == ALL_PERMISSION_KEYS
    assert (data["is_system"], data["is_admin"], data["can_edit"]) == (False, False, True)


async def test_unknown_permission_keys_are_rejected(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)
    custom = await custom_role(db_session, "Night auditor", {P.REPORT_VIEW})

    created = await client.post(
        ROLES, json={"name": "X", "permissions": [P.REPORT_VIEW, "rooms.fly", "admin.everything"]}
    )
    updated = await client.patch(f"{ROLES}/{custom.id}", json={"permissions": ["report.veiw"]})

    assert created.status_code == updated.status_code == 422
    assert created.json() == {
        "detail": "Unknown permission(s): admin.everything, rooms.fly.",
        "code": "unknown_permission",
    }
    assert await stored_keys(db_session, custom.id) == {P.REPORT_VIEW}


@pytest.mark.parametrize("name", ["Receptionist", "receptionist", " RECEPTIONIST "])
async def test_role_names_are_unique_ignoring_case(
    client: AsyncClient, login_as: LoginAs, name: str
) -> None:
    await login_as(Roles.ADMIN)

    response = await client.post(ROLES, json={"name": name})

    assert response.status_code == 409
    assert response.json()["code"] == "role_name_taken"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 101},
        {"name": "Ok", "description": "x" * 501},
        {"name": "Ok", "permissions": "dashboard.view"},
        {"name": "Ok", "copy_from_role_id": 0},
        {"name": "Ok", "is_system": True},
    ],
)
async def test_create_validation(
    client: AsyncClient, login_as: LoginAs, payload: dict[str, Any]
) -> None:
    await login_as(Roles.ADMIN)

    assert (await client.post(ROLES, json=payload)).status_code == 422


# --- update -----------------------------------------------------------------------------


async def test_rename_and_describe_a_custom_role(
    client: AsyncClient,
    login_as: LoginAs,
    db_session: AsyncSession,
    published: list[DomainEvent],
) -> None:
    await login_as(Roles.ADMIN)
    custom = await custom_role(db_session, "Night auditor", {P.REPORT_VIEW}, "Old")

    response = await client.patch(
        f"{ROLES}/{custom.id}", json={"name": "Night manager", "description": ""}
    )

    assert response.status_code == 200, response.text
    assert (response.json()["name"], response.json()["description"]) == ("Night manager", None)
    [row] = await role_audit(db_session)
    assert (row.action, row.before, row.after) == (
        AuditAction.ROLE_UPDATED,
        {"name": "Night auditor", "description": "Old"},
        {"name": "Night manager", "description": None},
    )
    assert published == []  # permissions unchanged


async def test_changing_permissions_records_the_diff_and_notifies_holders(
    client: AsyncClient,
    second_client: AsyncClient,
    login_as: LoginAs,
    make_user: MakeUser,
    db_session: AsyncSession,
    published: list[DomainEvent],
) -> None:
    await login_as(Roles.ADMIN)
    custom = await custom_role(
        db_session, "Night auditor", {P.DASHBOARD_VIEW, P.REPORT_VIEW, P.GUEST_VIEW}
    )
    holder = await make_user("owl", role="Night auditor")
    other = await make_user("lark", role="Night auditor")
    await make_user("bystander", role=Roles.RECEPTIONIST)
    await second_client.post(
        "/api/auth/login", json={"username": "owl", "password": "owl-password"}
    )

    response = await client.patch(
        f"{ROLES}/{custom.id}",
        json={"permissions": [P.DASHBOARD_VIEW, P.REPORT_VIEW, P.RESERVATION_VIEW, P.AUDIT_VIEW]},
    )

    assert response.status_code == 200, response.text
    assert await stored_keys(db_session, custom.id) == {
        P.DASHBOARD_VIEW,
        P.REPORT_VIEW,
        P.RESERVATION_VIEW,
        P.AUDIT_VIEW,
    }
    [row] = await role_audit(db_session)
    assert (row.before, row.after) == (
        None,
        {"permissions": {"added": [P.AUDIT_VIEW, P.RESERVATION_VIEW], "removed": [P.GUEST_VIEW]}},
    )
    [event] = published
    assert isinstance(event, PermissionsChanged)
    assert event.user_ids == sorted([holder.id, other.id])
    # The holder's existing session sees the new permissions straight away.
    me = (await second_client.get("/api/auth/me")).json()
    assert P.AUDIT_VIEW in me["permissions"] and P.GUEST_VIEW not in me["permissions"]


async def test_admin_role_cannot_be_changed_at_all(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)
    admin_role = await role_id(db_session, Roles.ADMIN)

    for payload in (
        {"name": "Superuser"},
        {"description": "New text"},
        {"permissions": [P.DASHBOARD_VIEW]},
        {},
    ):
        response = await client.patch(f"{ROLES}/{admin_role}", json=payload)
        assert response.status_code == 409, payload
        assert response.json()["code"] == "role_immutable"
    deleted = await client.delete(f"{ROLES}/{admin_role}")
    assert deleted.status_code == 409
    assert deleted.json()["code"] == "system_role"


async def test_system_roles_keep_name_and_description_but_permissions_can_change(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)
    receptionist = await role_id(db_session, Roles.RECEPTIONIST)

    renamed = await client.patch(f"{ROLES}/{receptionist}", json={"name": "Desk clerk"})
    described = await client.patch(f"{ROLES}/{receptionist}", json={"description": "Front desk"})
    # Sending the unchanged name and description along with new permissions is fine.
    edited = await client.patch(
        f"{ROLES}/{receptionist}",
        json={
            "name": Roles.RECEPTIONIST,
            "description": "Day-to-day front desk work",
            "permissions": sorted(DEFAULT_ROLES[3].permissions | {P.REPORT_VIEW}),
        },
    )

    assert (renamed.status_code, renamed.json()["code"]) == (409, "system_role")
    assert (described.status_code, described.json()["code"]) == (409, "system_role")
    assert edited.status_code == 200, edited.text
    assert edited.json()["description"] == "Day-to-day front desk work"
    assert P.REPORT_VIEW in edited.json()["permissions"]


async def test_cannot_add_permissions_you_do_not_have(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.GENERAL_MANAGER)
    custom = await custom_role(db_session, "Night auditor", {P.REPORT_VIEW})

    response = await client.patch(
        f"{ROLES}/{custom.id}", json={"permissions": [P.REPORT_VIEW, P.SETTINGS_MANAGE]}
    )

    assert response.status_code == 403
    assert response.json()["code"] == "permission_not_held"
    assert await stored_keys(db_session, custom.id) == {P.REPORT_VIEW}


async def test_cannot_edit_or_delete_a_role_above_you(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.GENERAL_MANAGER)
    custom = await custom_role(db_session, "Settings clerk", {P.SETTINGS_MANAGE, P.REPORT_VIEW})

    for response in (
        await client.patch(f"{ROLES}/{custom.id}", json={"description": "harmless?"}),
        # Even removing the key they lack: a more powerful role is not theirs to manage.
        await client.patch(f"{ROLES}/{custom.id}", json={"permissions": [P.REPORT_VIEW]}),
        await client.delete(f"{ROLES}/{custom.id}"),
    ):
        assert response.status_code == 403
        assert response.json()["code"] == "role_above_you"
    assert await stored_keys(db_session, custom.id) == {P.SETTINGS_MANAGE, P.REPORT_VIEW}


async def test_rename_to_an_existing_name(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)
    custom = await custom_role(db_session, "Night auditor", set())

    response = await client.patch(f"{ROLES}/{custom.id}", json={"name": "room attendant"})

    assert response.status_code == 409
    assert response.json()["code"] == "role_name_taken"


async def test_renaming_only_the_case_of_your_own_name_is_fine(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)
    custom = await custom_role(db_session, "Night auditor", set())

    response = await client.patch(f"{ROLES}/{custom.id}", json={"name": "Night Auditor"})

    assert response.status_code == 200
    assert response.json()["name"] == "Night Auditor"


async def test_no_op_update_changes_nothing(
    client: AsyncClient,
    login_as: LoginAs,
    db_session: AsyncSession,
    published: list[DomainEvent],
) -> None:
    await login_as(Roles.ADMIN)
    custom = await custom_role(db_session, "Night auditor", {P.REPORT_VIEW}, "Nights")

    response = await client.patch(
        f"{ROLES}/{custom.id}",
        json={"name": "Night auditor", "description": "Nights", "permissions": [P.REPORT_VIEW]},
    )

    assert response.status_code == 200
    assert await role_audit(db_session) == []
    assert published == []


@pytest.mark.parametrize(
    "payload",
    [
        {"name": None},
        {"name": ""},
        {"permissions": None},
        {"permissions": [1]},
        {"is_system": False},
    ],
)
async def test_update_validation(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession, payload: dict[str, Any]
) -> None:
    await login_as(Roles.ADMIN)
    custom = await custom_role(db_session, "Night auditor", set())

    assert (await client.patch(f"{ROLES}/{custom.id}", json=payload)).status_code == 422


async def test_unknown_role(client: AsyncClient, login_as: LoginAs) -> None:
    await login_as(Roles.ADMIN)

    for response in (
        await client.get(f"{ROLES}/999999"),
        await client.patch(f"{ROLES}/999999", json={"name": "x"}),
        await client.delete(f"{ROLES}/999999"),
    ):
        assert response.status_code == 404
    assert (await client.get(f"{ROLES}/0")).status_code == 422


# --- delete -----------------------------------------------------------------------------


async def test_delete_an_unused_custom_role(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    actor = await login_as(Roles.GENERAL_MANAGER)
    custom = await custom_role(db_session, "Night auditor", {P.REPORT_VIEW, P.DASHBOARD_VIEW}, "N")
    custom_id = custom.id

    response = await client.delete(f"{ROLES}/{custom_id}")

    assert response.status_code == 204
    assert (await client.get(f"{ROLES}/{custom_id}")).status_code == 404
    assert await stored_keys(db_session, custom_id) == set()
    [row] = await role_audit(db_session)
    assert (row.action, row.actor_user_id, row.after) == (AuditAction.ROLE_DELETED, actor.id, None)
    assert row.before == {
        "name": "Night auditor",
        "description": "N",
        "permissions": {"added": [], "removed": [P.DASHBOARD_VIEW, P.REPORT_VIEW]},
    }


async def test_cannot_delete_a_role_someone_has(
    client: AsyncClient, login_as: LoginAs, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)
    custom = await custom_role(db_session, "Night auditor", set())
    await make_user("owl", role="Night auditor", deactivated=True)

    response = await client.delete(f"{ROLES}/{custom.id}")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "1 user(s) still have the role 'Night auditor'. Give them another role first.",
        "code": "role_in_use",
    }


async def test_system_roles_cannot_be_deleted(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)

    response = await client.delete(f"{ROLES}/{await role_id(db_session, Roles.ROOM_ATTENDANT)}")

    assert response.status_code == 409
    assert response.json()["code"] == "system_role"
