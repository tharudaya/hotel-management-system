"""User administration API: every endpoint, every guard rule, search and pagination."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import ALL_PERMISSION_KEYS
from app.models import (
    AuditAction,
    AuditEvent,
    AuditLog,
    Property,
    Role,
    RolePermission,
    User,
    UserPropertyRole,
    UserSession,
)
from app.realtime.events import DomainEvent, PermissionsChanged
from app.services.permissions import get_default_property_id
from tests.conftest import MakeUser, Roles, assign_role, role_id

USERS = "/api/admin/users"


# --- helpers ---------------------------------------------------------------------------


async def login(client: AsyncClient, username: str) -> None:
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": f"{username}-password"}
    )
    assert response.status_code == 200, response.text


async def act_as(client: AsyncClient, make_user: MakeUser, role: str | None = Roles.ADMIN) -> User:
    """Create the acting user and log `client` in as them."""
    actor = await make_user("actor", role=role)
    await login(client, "actor")
    return actor


def assert_no_secrets(value: object) -> None:
    """No password hashes or session data anywhere in a response body."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "restrictions":
                continue  # action names ("reset_password") mapped to reasons, not data
            assert "password" not in key and "session" not in key and "token" not in key, key
            assert_no_secrets(item)
    elif isinstance(value, list):
        for item in value:
            assert_no_secrets(item)


async def audit(db: AsyncSession, action: AuditAction | None = None) -> list[AuditLog]:
    query = select(AuditLog).where(AuditLog.target_type == "user")
    if action is not None:
        query = query.where(AuditLog.action == action.value)
    return list((await db.scalars(query.order_by(AuditLog.id))).all())


async def make_role(db: AsyncSession, name: str, keys: frozenset[str]) -> Role:
    """A custom role at the default property holding exactly `keys`."""
    role = Role(property_id=await get_default_property_id(db), name=name, is_system=False)
    db.add(role)
    await db.flush()
    db.add_all(RolePermission(role_id=role.id, permission_key=k) for k in keys)
    await db.flush()
    return role


async def give_role(db: AsyncSession, user: User, role: Role) -> None:
    db.add(UserPropertyRole(user_id=user.id, property_id=role.property_id, role_id=role.id))
    await db.flush()


@pytest.fixture
def published(app: FastAPI) -> list[DomainEvent]:
    events: list[DomainEvent] = []

    async def collect(event: DomainEvent) -> None:
        events.append(event)

    app.state.event_bus.subscribe(collect)
    return events


def body(response: Response) -> Any:  # noqa: ANN401 - parsed JSON
    assert_no_secrets(response.json())
    return response.json()


# --- list: search, filters, sorting, pagination ----------------------------------------


@pytest.fixture
async def staff(db_session: AsyncSession, make_user: MakeUser) -> dict[str, User]:
    """A small, varied staff list (plus the actor, created by each test)."""
    people = {
        "alice": await make_user("alice", role=Roles.RECEPTIONIST),
        "bob": await make_user("bob", role=Roles.ROOM_ATTENDANT),
        "carol": await make_user("carol", role=Roles.RECEPTIONIST, deactivated=True),
        "dave": await make_user(
            "dave",
            role=Roles.HOUSEKEEPING_SUPERVISOR,
            expires_at=datetime.now(UTC) - timedelta(days=1),
        ),
        "erin": await make_user("erin", role=None),
    }
    people["alice"].full_name = "Alice Andersson"
    people["alice"].email = "alice@hotel.example"
    people["alice"].employee_id = "EMP-100"
    people["bob"].full_name = "Bob Berg"
    people["bob"].email = "bob@hotel.example"
    people["bob"].last_login_at = datetime(2026, 10, 1, tzinfo=UTC)
    people["carol"].full_name = "Carol Carlsson"
    people["dave"].full_name = "Dave Dahl"
    people["erin"].full_name = "Erin 50% Ek"
    people["erin"].password_hash = None  # invited
    await db_session.flush()
    return people


async def test_list_returns_users_with_status_and_role(
    client: AsyncClient, make_user: MakeUser, staff: dict[str, User]
) -> None:
    await act_as(client, make_user)

    data = body(await client.get(USERS))

    assert (data["total"], data["page"], data["page_size"]) == (6, 1, 25)
    by_name = {item["username"]: item for item in data["items"]}
    assert by_name["alice"]["status"] == "active"
    assert by_name["alice"]["role"]["name"] == "Receptionist"
    assert by_name["carol"]["status"] == "deactivated"
    assert by_name["dave"]["status"] == "expired"
    assert by_name["erin"]["status"] == "invited"
    assert by_name["erin"]["role"] is None
    assert [i["full_name"] for i in data["items"]][:3] == [
        "Actor",
        "Alice Andersson",
        "Bob Berg",
    ]  # default sort: name


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("andersson", ["alice"]),  # name, case-insensitive
        ("BOB", ["bob"]),  # username
        ("bob@hotel", ["bob"]),  # email
        ("emp-100", ["alice"]),  # employee id
        ("50%", ["erin"]),  # LIKE wildcards are matched literally
        ("_", []),
        ("nobody", []),
    ],
)
async def test_search(
    client: AsyncClient, make_user: MakeUser, staff: dict[str, User], q: str, expected: list[str]
) -> None:
    await act_as(client, make_user)

    data = body(await client.get(USERS, params={"q": q}))

    assert [i["username"] for i in data["items"]] == expected
    assert data["total"] == len(expected)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("active", ["actor", "alice", "bob"]),
        ("deactivated", ["carol"]),
        ("expired", ["dave"]),
        ("invited", ["erin"]),
    ],
)
async def test_filter_by_status(
    client: AsyncClient,
    make_user: MakeUser,
    staff: dict[str, User],
    status: str,
    expected: list[str],
) -> None:
    await act_as(client, make_user)

    data = body(await client.get(USERS, params={"status": status, "sort": "username"}))

    assert [i["username"] for i in data["items"]] == expected


async def test_filter_by_role(
    client: AsyncClient, make_user: MakeUser, staff: dict[str, User], db_session: AsyncSession
) -> None:
    await act_as(client, make_user)
    receptionist = await role_id(db_session, Roles.RECEPTIONIST)

    data = body(await client.get(USERS, params={"role_id": receptionist, "sort": "username"}))

    assert [i["username"] for i in data["items"]] == ["alice", "carol"]


async def test_sorting(client: AsyncClient, make_user: MakeUser, staff: dict[str, User]) -> None:
    await act_as(client, make_user)

    by_username_desc = body(await client.get(USERS, params={"sort": "-username"}))
    by_last_login = body(await client.get(USERS, params={"sort": "-last_login_at"}))

    assert [i["username"] for i in by_username_desc["items"]] == [
        "erin",
        "dave",
        "carol",
        "bob",
        "alice",
        "actor",
    ]
    # The actor just logged in, bob logged in earlier, the rest never (nulls last).
    assert [i["username"] for i in by_last_login["items"]][:2] == ["actor", "bob"]


async def test_pagination(client: AsyncClient, make_user: MakeUser, staff: dict[str, User]) -> None:
    await act_as(client, make_user)

    pages = [
        body(await client.get(USERS, params={"sort": "username", "page_size": 4, "page": n}))
        for n in (1, 2, 3)
    ]

    assert [[i["username"] for i in p["items"]] for p in pages] == [
        ["actor", "alice", "bob", "carol"],
        ["dave", "erin"],
        [],
    ]
    assert all(p["total"] == 6 for p in pages)


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page_size": 0},
        {"page_size": 101},
        {"sort": "password_hash"},
        {"status": "sleeping"},
        {"role_id": 0},
        {"q": "x" * 101},
        {"unknown": "1"},
    ],
)
async def test_list_validation(
    client: AsyncClient, make_user: MakeUser, params: dict[str, str | int]
) -> None:
    await act_as(client, make_user)

    assert (await client.get(USERS, params=params)).status_code == 422


async def test_list_needs_only_user_view(
    client: AsyncClient, make_user: MakeUser, staff: dict[str, User]
) -> None:
    await act_as(client, make_user, role=Roles.FRONT_OFFICE_MANAGER)  # user.view, no manage

    assert (await client.get(USERS)).status_code == 200
    assert (await client.post(USERS, json={"username": "x1x", "full_name": "X"})).status_code == 403


# --- get one -----------------------------------------------------------------------------


async def test_get_user_details_with_roles_per_property(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await act_as(client, make_user)
    target = await make_user(
        "alice", role=Roles.RECEPTIONIST, expires_at=datetime(2030, 1, 1, tzinfo=UTC)
    )
    other = Property(name="Second hotel", time_zone="Europe/Oslo")
    db_session.add(other)
    await db_session.flush()
    oslo_role = Role(property_id=other.id, name="Oslo desk", is_system=False)
    db_session.add(oslo_role)
    await db_session.flush()
    db_session.add(UserPropertyRole(user_id=target.id, property_id=other.id, role_id=oslo_role.id))
    await db_session.flush()

    data = body(await client.get(f"{USERS}/{target.id}"))

    assert data["username"] == "alice"
    assert data["status"] == "active"
    assert data["expires_at"].startswith("2030-01-01")
    assert data["last_login_at"] is None
    assert data["created_at"]
    assert [(r["property"]["name"], r["role"]["name"]) for r in data["roles"]] == [
        ("Grand Hotel Stockholm", "Receptionist")
        if r["property"]["id"] == data["roles"][0]["property"]["id"]
        else (r["property"]["name"], r["role"]["name"])
        for r in data["roles"]
    ]
    assert {r["role"]["name"] for r in data["roles"]} == {"Receptionist", "Oslo desk"}


async def test_get_user_not_found(client: AsyncClient, make_user: MakeUser) -> None:
    await act_as(client, make_user)

    response = await client.get(f"{USERS}/999999")

    assert response.status_code == 404
    assert response.json() == {"detail": "User 999999 not found", "code": "not_found"}


# --- create --------------------------------------------------------------------------------


async def test_create_invited_user(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    actor = await act_as(client, make_user)

    response = await client.post(
        USERS,
        json={
            "username": "  New.Person ",
            "full_name": "New Person",
            "email": "New.Person@Hotel.Example",
            "job_title": "Night auditor",
            "employee_id": "EMP-7",
        },
    )

    assert response.status_code == 201
    data = body(response)
    assert (data["username"], data["email"], data["status"]) == (
        "new.person",
        "new.person@hotel.example",
        "invited",
    )
    assert data["roles"] == []
    user = await db_session.get(User, data["id"])
    assert user is not None and user.password_hash is None
    [row] = await audit(db_session, AuditAction.USER_CREATED)
    assert (row.actor_user_id, row.target_id, row.before) == (actor.id, str(user.id), None)
    assert row.after is not None and row.after["username"] == "new.person"
    assert "password" not in str(row.after)


async def test_invited_user_cannot_log_in(
    client: AsyncClient, second_client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await act_as(client, make_user)
    await client.post(USERS, json={"username": "newbie", "full_name": "New"})

    response = await second_client.post(
        "/api/auth/login", json={"username": "newbie", "password": "anything"}
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid username or password"}
    event = await db_session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    assert event is not None and event.details == {"reason": "invited"}


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"username": "ALICE", "full_name": "Other Alice"}, "username_taken"),
        ({"username": "alice2", "full_name": "A", "email": "ALICE@hotel.example"}, "email_taken"),
    ],
)
async def test_create_rejects_duplicates_case_insensitively(
    client: AsyncClient,
    make_user: MakeUser,
    db_session: AsyncSession,
    payload: dict[str, str],
    code: str,
) -> None:
    await act_as(client, make_user)
    alice = await make_user("alice")
    alice.email = "alice@hotel.example"
    await db_session.flush()

    response = await client.post(USERS, json=payload)

    assert response.status_code == 409
    assert response.json()["code"] == code


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "ab", "full_name": "Too short"},
        {"username": "has space", "full_name": "X"},
        {"username": "ok_name", "full_name": ""},
        {"username": "ok_name", "full_name": "X", "email": "not-an-email"},
        {"username": "ok_name", "full_name": "X", "expires_at": "2030-01-01T00:00:00"},  # naive
        {"username": "ok_name", "full_name": "X", "password": "secret"},  # unknown field
        {"username": "ok_name"},
    ],
)
async def test_create_validation(
    client: AsyncClient, make_user: MakeUser, payload: dict[str, str]
) -> None:
    await act_as(client, make_user)

    assert (await client.post(USERS, json=payload)).status_code == 422


# --- update ----------------------------------------------------------------------------------


async def test_update_profile_fields(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await act_as(client, make_user)
    target = await make_user("alice")
    target.phone = "+46 8 123"
    await db_session.flush()

    response = await client.patch(
        f"{USERS}/{target.id}",
        json={
            "full_name": "Alice A",
            "email": "A@Hotel.Example",
            "phone": None,
            "job_title": "Lead",
        },
    )

    assert response.status_code == 200
    data = body(response)
    assert (data["full_name"], data["email"], data["phone"], data["job_title"]) == (
        "Alice A",
        "a@hotel.example",
        None,
        "Lead",
    )
    assert data["employee_id"] is None  # omitted: unchanged
    [row] = await audit(db_session, AuditAction.USER_UPDATED)
    assert row.before == {
        "full_name": "Alice",
        "email": None,
        "phone": "+46 8 123",
        "job_title": None,
    }
    assert row.after == {
        "full_name": "Alice A",
        "email": "a@hotel.example",
        "phone": None,
        "job_title": "Lead",
    }


async def test_update_without_changes_writes_no_audit(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await act_as(client, make_user)
    target = await make_user("alice")

    response = await client.patch(f"{USERS}/{target.id}", json={"full_name": "Alice"})

    assert response.status_code == 200
    assert await audit(db_session, AuditAction.USER_UPDATED) == []


async def test_update_rejects_taken_email(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await act_as(client, make_user)
    other = await make_user("bob")
    other.email = "bob@hotel.example"
    target = await make_user("alice")
    await db_session.flush()

    response = await client.patch(f"{USERS}/{target.id}", json={"email": "BOB@hotel.example"})

    assert response.status_code == 409
    assert response.json()["code"] == "email_taken"


@pytest.mark.parametrize(
    "payload",
    [{"full_name": None}, {"username": "renamed"}, {"email": "bad"}, {"password": "x"}],
)
async def test_update_validation(
    client: AsyncClient, make_user: MakeUser, payload: dict[str, object]
) -> None:
    await act_as(client, make_user)
    target = await make_user("alice")

    assert (await client.patch(f"{USERS}/{target.id}", json=payload)).status_code == 422


async def test_expiring_a_user_ends_their_session(
    client: AsyncClient, second_client: AsyncClient, make_user: MakeUser
) -> None:
    await act_as(client, make_user)
    target = await make_user("alice")
    await login(second_client, "alice")

    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    response = await client.patch(f"{USERS}/{target.id}", json={"expires_at": past})

    assert response.status_code == 200
    assert body(response)["status"] == "expired"
    assert (await second_client.get("/api/auth/me")).status_code == 401


async def test_cannot_expire_yourself(client: AsyncClient, make_user: MakeUser) -> None:
    actor = await act_as(client, make_user)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()

    response = await client.patch(f"{USERS}/{actor.id}", json={"expires_at": past})

    assert response.status_code == 409
    assert response.json()["code"] == "self_action"


# --- role ------------------------------------------------------------------------------------


async def test_assign_role_revokes_sessions_and_notifies(
    client: AsyncClient,
    second_client: AsyncClient,
    make_user: MakeUser,
    db_session: AsyncSession,
    published: list[DomainEvent],
) -> None:
    actor = await act_as(client, make_user)
    target = await make_user("alice", role=Roles.RECEPTIONIST)
    await login(second_client, "alice")
    supervisor = await role_id(db_session, Roles.HOUSEKEEPING_SUPERVISOR)

    response = await client.put(f"{USERS}/{target.id}/role", json={"role_id": supervisor})

    assert response.status_code == 200
    assert [r["role"]["name"] for r in body(response)["roles"]] == ["Housekeeping supervisor"]
    assert (await second_client.get("/api/auth/me")).status_code == 401  # logged out
    [row] = await audit(db_session, AuditAction.USER_ROLE_CHANGED)
    assert row.actor_user_id == actor.id
    assert row.before == {
        "role_id": await role_id(db_session, Roles.RECEPTIONIST),
        "role_name": "Receptionist",
    }
    assert row.after == {
        "role_id": supervisor,
        "role_name": "Housekeeping supervisor",
        "sessions_revoked": 1,
    }
    assert [e.type for e in published] == ["session_ended", "permissions_changed"]
    changed = published[1]
    assert isinstance(changed, PermissionsChanged) and changed.user_ids == [target.id]
    assignment = await db_session.scalar(
        select(UserPropertyRole).where(UserPropertyRole.user_id == target.id)
    )
    assert assignment is not None and assignment.assigned_by == actor.id


async def test_assign_first_role_to_invited_user(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await act_as(client, make_user)
    target = await make_user("newbie", role=None)

    response = await client.put(
        f"{USERS}/{target.id}/role", json={"role_id": await role_id(db_session, Roles.RECEPTIONIST)}
    )

    assert response.status_code == 200
    [row] = await audit(db_session, AuditAction.USER_ROLE_CHANGED)
    assert row.before == {"role_id": None, "role_name": None}


async def test_assigning_the_same_role_changes_nothing(
    client: AsyncClient, second_client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await act_as(client, make_user)
    target = await make_user("alice", role=Roles.RECEPTIONIST)
    await login(second_client, "alice")

    response = await client.put(
        f"{USERS}/{target.id}/role", json={"role_id": await role_id(db_session, Roles.RECEPTIONIST)}
    )

    assert response.status_code == 200
    assert await audit(db_session, AuditAction.USER_ROLE_CHANGED) == []
    assert (await second_client.get("/api/auth/me")).status_code == 200  # still logged in


async def test_cannot_change_your_own_role(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    actor = await act_as(client, make_user)

    response = await client.put(
        f"{USERS}/{actor.id}/role", json={"role_id": await role_id(db_session, Roles.RECEPTIONIST)}
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "You cannot change your own role.", "code": "self_action"}


async def test_cannot_assign_a_role_with_permissions_you_lack(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await act_as(client, make_user, role=Roles.GENERAL_MANAGER)  # lacks settings.manage
    target = await make_user("alice", role=Roles.RECEPTIONIST)

    response = await client.put(
        f"{USERS}/{target.id}/role", json={"role_id": await role_id(db_session, Roles.ADMIN)}
    )

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"
    assert "permissions you don't have" in response.json()["detail"]


async def test_can_assign_a_role_within_your_permissions(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await act_as(client, make_user, role=Roles.GENERAL_MANAGER)
    target = await make_user("alice", role=Roles.RECEPTIONIST)

    response = await client.put(
        f"{USERS}/{target.id}/role",
        json={"role_id": await role_id(db_session, Roles.FRONT_OFFICE_MANAGER)},
    )

    assert response.status_code == 200


@pytest.mark.parametrize("role_id_value", [999999, "other_property"])
async def test_assign_unknown_role(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession, role_id_value: object
) -> None:
    await act_as(client, make_user)
    target = await make_user("alice")
    if role_id_value == "other_property":
        other = Property(name="Second hotel", time_zone="Europe/Oslo")
        db_session.add(other)
        await db_session.flush()
        foreign = Role(property_id=other.id, name="Foreign", is_system=False)
        db_session.add(foreign)
        await db_session.flush()
        role_id_value = foreign.id

    response = await client.put(f"{USERS}/{target.id}/role", json={"role_id": role_id_value})

    assert response.status_code == 404


# --- managing users with more permissions than you ----------------------------------------


@pytest.mark.parametrize(
    ("method", "suffix", "payload"),
    [
        ("PATCH", "", {"job_title": "Demoted"}),
        ("PUT", "/role", "receptionist"),
        ("POST", "/deactivate", None),
        ("POST", "/revoke-sessions", None),
    ],
)
async def test_cannot_manage_a_user_with_more_permissions(
    client: AsyncClient,
    make_user: MakeUser,
    db_session: AsyncSession,
    method: str,
    suffix: str,
    payload: object,
) -> None:
    await act_as(client, make_user, role=Roles.GENERAL_MANAGER)
    admin = await make_user("boss", role=Roles.ADMIN)
    if payload == "receptionist":
        payload = {"role_id": await role_id(db_session, Roles.RECEPTIONIST)}

    response = await client.request(method, f"{USERS}/{admin.id}{suffix}", json=payload)

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"
    assert await audit(db_session) == []


# --- last active Admin ------------------------------------------------------------------------


@pytest.fixture
async def owner_and_last_admin(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> User:
    """Actor with a custom all-permissions role (not Admin); exactly one active Admin."""
    owner = await make_role(db_session, "Owner", ALL_PERMISSION_KEYS)
    actor = await make_user("actor", role=None)
    await give_role(db_session, actor, owner)
    await login(client, "actor")
    return await make_user("boss", role=Roles.ADMIN)


@pytest.mark.parametrize(
    ("method", "suffix", "payload"),
    [
        ("POST", "/deactivate", None),
        ("PUT", "/role", "receptionist"),
        ("PATCH", "", {"expires_at": "2030-01-01T00:00:00+00:00"}),
    ],
)
async def test_cannot_remove_the_last_active_admin(
    client: AsyncClient,
    db_session: AsyncSession,
    owner_and_last_admin: User,
    method: str,
    suffix: str,
    payload: object,
) -> None:
    if payload == "receptionist":
        payload = {"role_id": await role_id(db_session, Roles.RECEPTIONIST)}

    response = await client.request(
        method, f"{USERS}/{owner_and_last_admin.id}{suffix}", json=payload
    )

    assert response.status_code == 409
    assert response.json()["code"] == "last_admin"
    assert "last active Admin" in response.json()["detail"]


async def test_admin_can_be_removed_when_another_active_admin_remains(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession, owner_and_last_admin: User
) -> None:
    await make_user("boss2", role=Roles.ADMIN)

    response = await client.post(f"{USERS}/{owner_and_last_admin.id}/deactivate")

    assert response.status_code == 200


@pytest.mark.parametrize("kind", ["invited", "deactivated", "expired"])
async def test_inactive_admins_do_not_count(
    client: AsyncClient,
    make_user: MakeUser,
    db_session: AsyncSession,
    owner_and_last_admin: User,
    kind: str,
) -> None:
    other = await make_user(
        "boss2",
        role=Roles.ADMIN,
        deactivated=kind == "deactivated",
        expires_at=datetime.now(UTC) - timedelta(days=1) if kind == "expired" else None,
    )
    if kind == "invited":
        other.password_hash = None
        await db_session.flush()

    response = await client.post(f"{USERS}/{owner_and_last_admin.id}/deactivate")

    assert response.status_code == 409
    assert response.json()["code"] == "last_admin"


# --- deactivate / reactivate -------------------------------------------------------------------


async def test_deactivate_revokes_sessions(
    client: AsyncClient,
    second_client: AsyncClient,
    make_user: MakeUser,
    db_session: AsyncSession,
    published: list[DomainEvent],
) -> None:
    actor = await act_as(client, make_user)
    target = await make_user("alice")
    await login(second_client, "alice")

    response = await client.post(f"{USERS}/{target.id}/deactivate")

    assert response.status_code == 200
    data = body(response)
    assert data["status"] == "deactivated" and data["deactivated_at"]
    assert (await second_client.get("/api/auth/me")).status_code == 401
    await db_session.refresh(target)
    assert target.deactivated_by == actor.id
    [row] = await audit(db_session, AuditAction.USER_DEACTIVATED)
    assert row.before == {"deactivated_at": None}
    assert row.after is not None and row.after["sessions_revoked"] == 1
    assert {e.type for e in published} == {"session_ended", "permissions_changed"}


async def test_cannot_deactivate_yourself(client: AsyncClient, make_user: MakeUser) -> None:
    actor = await act_as(client, make_user)

    response = await client.post(f"{USERS}/{actor.id}/deactivate")

    assert response.status_code == 409
    assert response.json() == {"detail": "You cannot deactivate yourself.", "code": "self_action"}


async def test_deactivate_twice(client: AsyncClient, make_user: MakeUser) -> None:
    await act_as(client, make_user)
    target = await make_user("alice", deactivated=True)

    response = await client.post(f"{USERS}/{target.id}/deactivate")

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_transition"


async def test_reactivate(
    client: AsyncClient, second_client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await act_as(client, make_user)
    target = await make_user("alice", deactivated=True)

    response = await client.post(f"{USERS}/{target.id}/reactivate")

    assert response.status_code == 200
    assert body(response)["status"] == "active"
    await login(second_client, "alice")  # can sign in again
    [row] = await audit(db_session, AuditAction.USER_REACTIVATED)
    assert row.after == {"deactivated_at": None}


async def test_reactivate_active_user(client: AsyncClient, make_user: MakeUser) -> None:
    await act_as(client, make_user)
    target = await make_user("alice")

    response = await client.post(f"{USERS}/{target.id}/reactivate")

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_transition"


# --- revoke sessions -----------------------------------------------------------------------------


async def test_revoke_sessions(
    client: AsyncClient,
    second_client: AsyncClient,
    make_user: MakeUser,
    db_session: AsyncSession,
    published: list[DomainEvent],
) -> None:
    await act_as(client, make_user)
    target = await make_user("alice")
    await login(second_client, "alice")

    response = await client.post(f"{USERS}/{target.id}/revoke-sessions")

    assert response.status_code == 200
    assert response.json() == {"revoked_sessions": 1}
    assert (await second_client.get("/api/auth/me")).status_code == 401
    active = await db_session.scalar(
        select(UserSession.id).where(
            UserSession.user_id == target.id, UserSession.revoked_at.is_(None)
        )
    )
    assert active is None
    [row] = await audit(db_session, AuditAction.USER_SESSIONS_REVOKED)
    assert row.after == {"sessions_revoked": 1}
    assert [e.type for e in published] == ["session_ended"]


async def test_revoke_your_own_sessions(client: AsyncClient, make_user: MakeUser) -> None:
    actor = await act_as(client, make_user)

    response = await client.post(f"{USERS}/{actor.id}/revoke-sessions")

    assert response.json() == {"revoked_sessions": 1}
    assert (await client.get("/api/auth/me")).status_code == 401


# --- every change writes exactly one audit row ---------------------------------------------------


async def test_one_audit_row_per_change(
    client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    await act_as(client, make_user)
    created = (await client.post(USERS, json={"username": "temp", "full_name": "Temp"})).json()
    uid = created["id"]
    await client.patch(f"{USERS}/{uid}", json={"job_title": "Helper"})
    await client.put(
        f"{USERS}/{uid}/role", json={"role_id": await role_id(db_session, Roles.ROOM_ATTENDANT)}
    )
    await client.post(f"{USERS}/{uid}/deactivate")
    await client.post(f"{USERS}/{uid}/reactivate")
    await client.post(f"{USERS}/{uid}/revoke-sessions")

    rows = await audit(db_session)

    assert [r.action for r in rows] == [
        "user.created",
        "user.updated",
        "user.role_changed",
        "user.deactivated",
        "user.reactivated",
        "user.sessions_revoked",
    ]
    assert {r.target_id for r in rows} == {str(uid)}
    assert all(r.property_id is not None and r.ip == "127.0.0.1" for r in rows)


async def test_role_assignment_helper_matches_api(
    make_user: MakeUser, db_session: AsyncSession
) -> None:
    """Sanity check for the test helpers used above."""
    user = await make_user("plain", role=None)
    await assign_role(db_session, user, Roles.RECEPTIONIST)

    assert await db_session.scalar(
        select(UserPropertyRole.role_id).where(UserPropertyRole.user_id == user.id)
    ) == await role_id(db_session, Roles.RECEPTIONIST)
