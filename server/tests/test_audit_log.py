"""The audit trail: one newest-first stream of data changes and sign-in events,
filters, cursor pagination, consistent action names, and rows that cannot change."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditAction, AuditEvent, AuditEventType, AuditLog, Property, User
from app.services.permissions import get_default_property_id
from tests.conftest import LoginAs, MakeUser, Roles, role_id

AUDIT = "/api/admin/audit"
USERS = "/api/admin/users"
ROLES = "/api/admin/roles"


async def entries(client: AsyncClient, **params: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    response = await client.get(AUDIT, params=params)
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def actions(client: AsyncClient, **params: Any) -> list[str]:  # noqa: ANN401
    return [e["action"] for e in await entries(client, **params)]


async def add_change(
    db: AsyncSession, at: datetime, action: str = "user.updated", target_id: str = "1"
) -> None:
    db.add(
        AuditLog(
            property_id=await get_default_property_id(db),
            action=action,
            target_type="user",
            target_id=target_id,
            after={"job_title": at.isoformat()},
            created_at=at,
        )
    )
    await db.flush()


# --- one stream ----------------------------------------------------------------------------


async def test_changes_and_sign_ins_in_one_newest_first_stream(
    client: AsyncClient, login_as: LoginAs, make_user: MakeUser
) -> None:
    admin = await login_as(Roles.ADMIN)
    alice = await make_user("alice")
    await client.patch(f"{USERS}/{alice.id}", json={"job_title": "Night audit"})

    items = await entries(client)

    assert [e["action"] for e in items] == ["user.updated", "auth.login_succeeded"]
    change, login = items
    assert change["source"] == "change"
    assert change["actor"] == {"id": admin.id, "username": "admin", "full_name": admin.full_name}
    assert (change["target_type"], change["target_id"]) == ("user", str(alice.id))
    assert change["target_label"] == alice.full_name
    assert (change["before"], change["after"]) == (
        {"job_title": None},
        {"job_title": "Night audit"},
    )
    # A sign-in is by and about the user who signed in.
    assert login["source"] == "auth"
    assert login["actor"]["username"] == "admin"
    assert (login["target_type"], login["target_id"]) == ("user", str(admin.id))
    assert login["before"] is login["after"] is None
    assert login["ip"] is not None
    assert len({e["id"] for e in items}) == 2


async def test_failed_sign_in_with_an_unknown_username(
    client: AsyncClient, login_as: LoginAs
) -> None:
    await client.post("/api/auth/login", json={"username": "Mallory", "password": "guess"})
    await login_as(Roles.ADMIN)

    failed = (await entries(client, action="auth.login_failed"))[0]

    assert failed["actor"] == {"id": None, "username": "mallory", "full_name": None}
    assert (failed["target_type"], failed["target_id"]) == (None, None)
    assert failed["details"] == {"reason": "unknown_user"}


# --- filters --------------------------------------------------------------------------------


async def test_filter_by_actor(
    client: AsyncClient, login_as: LoginAs, make_user: MakeUser, second_client: AsyncClient
) -> None:
    await login_as(Roles.ADMIN)
    await make_user("gm", role=Roles.GENERAL_MANAGER)
    await second_client.post("/api/auth/login", json={"username": "gm", "password": "gm-password"})
    alice = await make_user("alice")
    await second_client.patch(f"{USERS}/{alice.id}", json={"phone": "1"})
    await client.patch(f"{USERS}/{alice.id}", json={"phone": "2"})

    by_gm = await entries(client, actor="GM")  # case-insensitive

    assert [e["action"] for e in by_gm] == ["user.updated", "auth.login_succeeded"]
    assert by_gm[0]["after"] == {"phone": "1"}


@pytest.mark.parametrize(
    ("action", "expected"),
    [("user.updated", ["user.updated"]), ("auth.login_succeeded", ["auth.login_succeeded"])],
)
async def test_filter_by_action(
    client: AsyncClient, login_as: LoginAs, make_user: MakeUser, action: str, expected: list[str]
) -> None:
    await login_as(Roles.ADMIN)
    alice = await make_user("alice")
    await client.patch(f"{USERS}/{alice.id}", json={"phone": "1"})

    assert await actions(client, action=action) == expected


async def test_filter_by_target(
    client: AsyncClient,
    login_as: LoginAs,
    make_user: MakeUser,
    second_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await login_as(Roles.ADMIN)
    alice = await make_user("alice")
    await second_client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-password"}
    )
    await client.patch(f"{USERS}/{alice.id}", json={"phone": "1"})
    created = await client.post(ROLES, json={"name": "Night auditor"})
    night = created.json()["id"]

    # A user's trail includes their sign-ins.
    assert await actions(client, target_type="user", target_id=str(alice.id)) == [
        "user.updated",
        "auth.login_succeeded",
    ]
    assert await actions(client, target_type="role", target_id=str(night)) == ["role.created"]
    # A deleted role keeps its name in the trail.
    await client.delete(f"{ROLES}/{night}")
    deleted = (await entries(client, target_type="role", target_id=str(night)))[0]
    assert (deleted["action"], deleted["target_label"]) == ("role.deleted", "Night auditor")
    assert await actions(client, target_type="role") == ["role.deleted", "role.created"]
    assert await actions(client, target_type="reservation") == []


async def test_filter_by_dates_in_the_property_time_zone(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)  # its sign-in is "now", far from these dates
    # Stockholm is UTC+2 in October: local midnight of the 10th is 22:00 UTC on the 9th.
    await add_change(db_session, datetime(2026, 10, 9, 21, 59, tzinfo=UTC), target_id="9")
    await add_change(db_session, datetime(2026, 10, 9, 22, 0, tzinfo=UTC), target_id="10")
    await add_change(db_session, datetime(2026, 10, 10, 21, 59, tzinfo=UTC), target_id="10b")
    await add_change(db_session, datetime(2026, 10, 10, 22, 0, tzinfo=UTC), target_id="11")

    on_the_10th = await entries(client, date_from="2026-10-10", date_to="2026-10-10")
    from_the_11th = await entries(client, date_from="2026-10-11", target_type="user")
    until_the_9th = await entries(client, date_to="2026-10-09")

    assert [e["target_id"] for e in on_the_10th] == ["10b", "10"]
    assert [e["target_id"] for e in from_the_11th][-1] == "11"
    assert [e["target_id"] for e in until_the_9th] == ["9"]


@pytest.mark.parametrize(
    "params",
    [
        {"action": "user.exploded"},
        {"target_type": "spaceship"},
        {"date_from": "2026-10-12", "date_to": "2026-10-11"},
        {"date_from": "yesterday"},
        {"limit": 0},
        {"limit": 101},
        {"cursor": "not-a-cursor"},
        {"page": 2},
    ],
)
async def test_validation(client: AsyncClient, login_as: LoginAs, params: dict[str, Any]) -> None:
    await login_as(Roles.ADMIN)

    assert (await client.get(AUDIT, params=params)).status_code == 422


async def test_other_properties_changes_are_not_shown(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)
    other = Property(name="Other Hotel", time_zone="UTC")
    db_session.add(other)
    await db_session.flush()
    db_session.add(
        AuditLog(property_id=other.id, action="user.updated", target_type="user", target_id="1")
    )
    await db_session.flush()

    assert await actions(client, action="user.updated") == []


async def test_needs_audit_view(client: AsyncClient, login_as: LoginAs) -> None:
    await login_as(Roles.FRONT_OFFICE_MANAGER)

    assert (await client.get(AUDIT)).status_code == 403


# --- pagination -----------------------------------------------------------------------------


async def test_cursor_pages_have_no_gaps_or_repeats(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)
    base = datetime(2026, 10, 1, 12, tzinfo=UTC)
    # Seven changes, three sharing one timestamp (ties are ordered by id).
    for i, minutes in enumerate([0, 1, 2, 2, 2, 3, 4]):
        await add_change(db_session, base + timedelta(minutes=minutes), target_id=str(i))

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str | int] = {
            "limit": 3,
            "target_type": "user",
            "action": "user.updated",
        }
        if cursor:
            params["cursor"] = cursor
        body = (await client.get(AUDIT, params=params)).json()
        seen.extend(e["target_id"] for e in body["items"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert pages == 3
    assert seen == ["6", "5", "4", "3", "2", "1", "0"]


async def test_new_entries_do_not_shift_later_pages(
    client: AsyncClient, login_as: LoginAs, db_session: AsyncSession
) -> None:
    await login_as(Roles.ADMIN)
    base = datetime(2026, 10, 1, 12, tzinfo=UTC)
    for i in range(4):
        await add_change(db_session, base + timedelta(minutes=i), target_id=str(i))
    params: dict[str, str | int] = {"limit": 2, "action": "user.updated"}
    first = (await client.get(AUDIT, params=params)).json()

    await add_change(db_session, base + timedelta(hours=1), target_id="new")
    second = (await client.get(AUDIT, params={**params, "cursor": first["next_cursor"]})).json()

    assert [e["target_id"] for e in first["items"]] == ["3", "2"]
    assert [e["target_id"] for e in second["items"]] == ["1", "0"]
    assert second["next_cursor"] is None


# --- rows cannot be modified --------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_log SET action = 'user.nothing_happened'",
        "DELETE FROM audit_log",
        "UPDATE audit_events SET username = 'someone_else'",
        "DELETE FROM audit_events",
    ],
)
async def test_audit_rows_cannot_be_changed_or_deleted(
    login_as: LoginAs, make_user: MakeUser, db_session: AsyncSession, statement: str
) -> None:
    await login_as(Roles.ADMIN)  # writes an audit_events row
    await add_change(db_session, datetime.now(UTC))

    with pytest.raises(DBAPIError, match="cannot be changed or deleted"):
        async with db_session.begin_nested():
            await db_session.execute(text(statement))


async def test_orm_updates_are_refused_too(login_as: LoginAs, db_session: AsyncSession) -> None:
    await login_as(Roles.ADMIN)
    await add_change(db_session, datetime.now(UTC))

    for stmt in (
        update(AuditLog).values(after=None),
        delete(AuditEvent),
        update(AuditEvent).values(occurred_at=datetime.now(UTC) - timedelta(days=1)),
    ):
        with pytest.raises(DBAPIError, match="cannot be changed or deleted"):
            async with db_session.begin_nested():
                await db_session.execute(stmt)


async def test_users_with_a_trail_cannot_be_deleted(
    login_as: LoginAs, db_session: AsyncSession
) -> None:
    admin = await login_as(Roles.ADMIN)  # has audit_events rows (the sign-in)

    # ON DELETE SET NULL would rewrite the audit rows: refused. (Users are never deleted.)
    with pytest.raises(DBAPIError, match="cannot be changed or deleted"):
        async with db_session.begin_nested():
            await db_session.execute(delete(User).where(User.id == admin.id))


async def test_there_are_no_write_endpoints(client: AsyncClient, login_as: LoginAs) -> None:
    await login_as(Roles.ADMIN)

    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert (await client.request(method, AUDIT)).status_code == 405
        assert (await client.request(method, f"{AUDIT}/1")).status_code in (404, 405)


# --- every admin action and sign-in is logged, with a clear name -------------------------


def test_action_names_follow_one_pattern() -> None:
    names = [a.value for a in AuditAction] + [e.value for e in AuditEventType]
    pattern = re.compile(r"^[a-z]+(_[a-z]+)*\.[a-z]+(_[a-z]+)*$")  # area.past_tense_event
    assert [n for n in names if not pattern.match(n)] == []
    assert len(names) == len(set(names))


async def test_admin_actions_and_sign_ins_are_all_logged(
    client: AsyncClient,
    second_client: AsyncClient,
    login_as: LoginAs,
    make_user: MakeUser,
    db_session: AsyncSession,
) -> None:
    """Walk through steps 3 to 5 (users, links, roles) plus sign-in, sign-out and a
    failed sign-in; each leaves exactly one entry with the expected name."""
    admin = await login_as(Roles.ADMIN)
    receptionist = await role_id(db_session, Roles.RECEPTIONIST)
    attendant = await role_id(db_session, Roles.ROOM_ATTENDANT)
    expected: list[tuple[str, str | None]] = [("auth.login_succeeded", "admin")]

    async def check(action: str, actor: str | None) -> None:
        expected.append((action, actor))
        newest = (await entries(client, limit=1))[0]
        assert (newest["action"], newest["actor"] and newest["actor"]["username"]) == (
            action,
            actor,
        )

    # Users (step 3).
    created = await client.post(
        USERS, json={"username": "carl", "full_name": "Carl", "role_id": receptionist}
    )
    carl = created.json()["id"]
    await check(AuditAction.USER_CREATED, "admin")
    await client.patch(f"{USERS}/{carl}", json={"job_title": "Porter"})
    await check(AuditAction.USER_UPDATED, "admin")
    await client.put(f"{USERS}/{carl}/role", json={"role_id": attendant})
    await check(AuditAction.USER_ROLE_CHANGED, "admin")
    await client.post(f"{USERS}/{carl}/deactivate")
    await check(AuditAction.USER_DEACTIVATED, "admin")
    await client.post(f"{USERS}/{carl}/reactivate")
    await check(AuditAction.USER_REACTIVATED, "admin")
    await client.post(f"{USERS}/{carl}/revoke-sessions")
    await check(AuditAction.USER_SESSIONS_REVOKED, "admin")

    # Invite, activation, reset (step 4); activation and reset are by the user.
    invite = (await client.post(f"{USERS}/{carl}/invite")).json()["link"]
    await check(AuditAction.USER_INVITE_SENT, "admin")
    await second_client.post(
        "/api/auth/activate",
        json={"token": invite.rsplit("#", 1)[1], "password": "Tr1cky-Ferret-Lamp"},
    )
    await check(AuditAction.USER_ACTIVATED, "carl")
    reset = (await client.post(f"{USERS}/{carl}/reset-password")).json()["link"]
    await check(AuditAction.USER_PASSWORD_RESET_SENT, "admin")
    await second_client.post(
        "/api/auth/reset-password",
        json={"token": reset.rsplit("#", 1)[1], "password": "Brave-Otter-Kettle"},
    )
    await check(AuditAction.USER_PASSWORD_RESET, "carl")

    # Roles (step 5).
    role = (await client.post(ROLES, json={"name": "Night auditor"})).json()["id"]
    await check(AuditAction.ROLE_CREATED, "admin")
    await client.patch(f"{ROLES}/{role}", json={"permissions": ["report.view"]})
    await check(AuditAction.ROLE_UPDATED, "admin")
    await client.delete(f"{ROLES}/{role}")
    await check(AuditAction.ROLE_DELETED, "admin")

    # Sign-in, failed sign-in, sign-out.
    await second_client.post(
        "/api/auth/login", json={"username": "carl", "password": "Brave-Otter-Kettle"}
    )
    await check(AuditEventType.LOGIN_SUCCEEDED, "carl")
    await second_client.post("/api/auth/login", json={"username": "carl", "password": "nope"})
    await check(AuditEventType.LOGIN_FAILED, "carl")
    await client.post("/api/auth/logout")
    expected.append((AuditEventType.LOGOUT, "admin"))

    # The whole trail, in order (read by another admin, since we signed out).
    await make_user("auditor", role=Roles.ADMIN)
    await client.post(
        "/api/auth/login", json={"username": "auditor", "password": "auditor-password"}
    )
    trail = [
        (e["action"], e["actor"] and e["actor"]["username"])
        for e in reversed(await entries(client, limit=100))
    ]
    assert trail[: len(expected)] == expected
    assert admin.id is not None
