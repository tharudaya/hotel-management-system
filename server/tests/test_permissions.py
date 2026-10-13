"""Permission catalog, its sync to the database, and effective permissions."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import (
    ADMIN_ROLE_NAME,
    ALL_PERMISSION_KEYS,
    DEFAULT_ROLES,
    PERMISSIONS,
    P,
)
from app.core.permissions import Permission as CatalogPermission
from app.models import Permission, Property, Role, RolePermission, UserPropertyRole
from app.services.permissions import (
    SyncResult,
    get_default_property_id,
    get_effective_permissions,
    sync_permission_catalog,
)
from tests.conftest import MakeUser, Roles, role_id

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {r.name: r.permissions for r in DEFAULT_ROLES}


async def keys_in_db(db: AsyncSession) -> set[str]:
    return set((await db.scalars(select(Permission.key))).all())


async def role_keys(db: AsyncSession, role_name: str) -> set[str]:
    rid = await role_id(db, role_name)
    rows = await db.scalars(
        select(RolePermission.permission_key).where(RolePermission.role_id == rid)
    )
    return set(rows.all())


# --- catalog ---------------------------------------------------------------------------


def test_catalog_keys_are_unique_and_grouped() -> None:
    keys = [p.key for p in PERMISSIONS]
    assert len(keys) == len(set(keys)) == 21
    assert all(p.group and p.description for p in PERMISSIONS)


def test_default_roles_only_use_catalog_keys() -> None:
    for role in DEFAULT_ROLES:
        assert role.permissions <= ALL_PERMISSION_KEYS, role.name


def test_default_role_definitions() -> None:
    assert ROLE_PERMISSIONS[Roles.ADMIN] == ALL_PERMISSION_KEYS
    assert ROLE_PERMISSIONS[Roles.GENERAL_MANAGER] == ALL_PERMISSION_KEYS - {
        P.SETTINGS_MANAGE,
        P.ROOM_MANAGE,
    }
    # Room setup is Admin-only.
    assert [name for name, keys in ROLE_PERMISSIONS.items() if P.ROOM_MANAGE in keys] == [
        Roles.ADMIN
    ]
    assert ROLE_PERMISSIONS[Roles.ROOM_ATTENDANT] == {P.ROOM_UPDATE_HOUSEKEEPING_STATUS}
    assert ROLE_PERMISSIONS[Roles.HOUSEKEEPING_SUPERVISOR] == {
        P.DASHBOARD_VIEW,
        P.ROOM_UPDATE_HOUSEKEEPING_STATUS,
        P.ROOM_SET_OUT_OF_ORDER,
    }
    front_office = ROLE_PERMISSIONS[Roles.FRONT_OFFICE_MANAGER]
    assert {P.PAYMENT_REFUND, P.ROOM_SET_OUT_OF_ORDER, P.REPORT_VIEW, P.USER_VIEW} <= front_office
    assert not front_office & {P.USER_MANAGE, P.ROLE_VIEW, P.AUDIT_VIEW, P.SETTINGS_MANAGE}
    assert len(ROLE_PERMISSIONS[Roles.RECEPTIONIST]) == 10


# --- sync ------------------------------------------------------------------------------


async def test_database_matches_catalog_after_migration(db_session: AsyncSession) -> None:
    assert await keys_in_db(db_session) == ALL_PERMISSION_KEYS
    for role in DEFAULT_ROLES:
        assert await role_keys(db_session, role.name) == role.permissions, role.name


async def test_sync_is_a_no_op_when_nothing_changed(db_session: AsyncSession) -> None:
    result = await sync_permission_catalog(db_session)

    assert result == SyncResult(added=frozenset(), removed=frozenset())
    assert await keys_in_db(db_session) == ALL_PERMISSION_KEYS


async def test_sync_adds_new_keys_and_grants_them_to_admin_only(
    db_session: AsyncSession,
) -> None:
    catalog = (*PERMISSIONS, CatalogPermission("report.export", "Export reports", "reports"))

    result = await sync_permission_catalog(db_session, catalog)

    assert result == SyncResult(added=frozenset({"report.export"}), removed=frozenset())
    assert "report.export" in await keys_in_db(db_session)
    assert "report.export" in await role_keys(db_session, Roles.ADMIN)
    # Other roles, including General manager ("all except settings"), are not changed.
    assert "report.export" not in await role_keys(db_session, Roles.GENERAL_MANAGER)


async def test_sync_removes_dropped_keys_and_their_role_grants(db_session: AsyncSession) -> None:
    catalog = [p for p in PERMISSIONS if p.key != P.PAYMENT_REFUND]
    assert P.PAYMENT_REFUND in await role_keys(db_session, Roles.GENERAL_MANAGER)

    result = await sync_permission_catalog(db_session, catalog)

    assert result == SyncResult(added=frozenset(), removed=frozenset({P.PAYMENT_REFUND}))
    assert P.PAYMENT_REFUND not in await keys_in_db(db_session)
    remaining = await db_session.scalar(
        select(RolePermission.role_id).where(RolePermission.permission_key == P.PAYMENT_REFUND)
    )
    assert remaining is None


async def test_sync_updates_changed_descriptions(db_session: AsyncSession) -> None:
    catalog = [
        CatalogPermission(p.key, "Look at the dashboard", p.group)
        if p.key == P.DASHBOARD_VIEW
        else p
        for p in PERMISSIONS
    ]

    await sync_permission_catalog(db_session, catalog)

    row = await db_session.get(Permission, P.DASHBOARD_VIEW, populate_existing=True)
    assert row is not None
    assert row.description == "Look at the dashboard"


async def test_sync_restores_missing_admin_grants(db_session: AsyncSession) -> None:
    admin = await role_id(db_session, Roles.ADMIN)
    await db_session.execute(delete(RolePermission).where(RolePermission.role_id == admin))

    await sync_permission_catalog(db_session)

    assert await role_keys(db_session, Roles.ADMIN) == ALL_PERMISSION_KEYS


# --- effective permissions -----------------------------------------------------------


async def effective(db: AsyncSession, user_id: int, **kwargs: datetime) -> frozenset[str]:
    return await get_effective_permissions(db, user_id, await get_default_property_id(db), **kwargs)


@pytest.mark.parametrize("role", [r.name for r in DEFAULT_ROLES])
async def test_effective_permissions_of_each_default_role(
    db_session: AsyncSession, make_user: MakeUser, role: str
) -> None:
    user = await make_user("someone", role=role)

    assert await effective(db_session, user.id) == ROLE_PERMISSIONS[role]


async def test_deactivated_user_has_no_permissions(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user = await make_user("someone", role=Roles.GENERAL_MANAGER, deactivated=True)

    assert await effective(db_session, user.id) == frozenset()


async def test_expired_user_has_no_permissions(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    yesterday = datetime.now(UTC) - timedelta(days=1)
    user = await make_user("temp", role=Roles.RECEPTIONIST, expires_at=yesterday)

    assert await effective(db_session, user.id) == frozenset()


async def test_expiry_is_evaluated_at_the_given_time(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    expires = datetime(2026, 6, 1, tzinfo=UTC)
    user = await make_user("temp", role=Roles.RECEPTIONIST, expires_at=expires)

    before = await effective(db_session, user.id, now=expires - timedelta(seconds=1))
    at = await effective(db_session, user.id, now=expires)

    assert before == ROLE_PERMISSIONS[Roles.RECEPTIONIST]
    assert at == frozenset()


async def test_user_without_a_role_has_no_permissions(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user = await make_user("nobody", role=None)

    assert await effective(db_session, user.id) == frozenset()


async def test_permissions_are_per_property(db_session: AsyncSession, make_user: MakeUser) -> None:
    user = await make_user("someone", role=Roles.ADMIN)
    other = Property(name="Second hotel", time_zone="Europe/Oslo")
    db_session.add(other)
    await db_session.flush()

    assert await get_effective_permissions(db_session, user.id, other.id) == frozenset()


async def test_admin_always_has_the_whole_catalog(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user = await make_user("boss", role=Roles.ADMIN)
    admin = await role_id(db_session, Roles.ADMIN)
    await db_session.execute(delete(RolePermission).where(RolePermission.role_id == admin))

    assert await effective(db_session, user.id) == ALL_PERMISSION_KEYS


async def test_custom_role_permissions_and_unknown_keys_ignored(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    property_id = await get_default_property_id(db_session)
    role = Role(property_id=property_id, name="Night auditor", description=None)
    db_session.add_all([role, Permission(key="legacy.key", description="Old", group="old")])
    await db_session.flush()
    db_session.add_all(
        RolePermission(role_id=role.id, permission_key=key)
        for key in (P.REPORT_VIEW, P.AUDIT_VIEW, "legacy.key")
    )
    user = await make_user("auditor", role=None)
    db_session.add(UserPropertyRole(user_id=user.id, property_id=property_id, role_id=role.id))
    await db_session.flush()

    # "legacy.key" is in the table but no longer in the code catalog.
    assert await effective(db_session, user.id) == {P.REPORT_VIEW, P.AUDIT_VIEW}


async def test_a_custom_role_named_like_admin_gets_nothing_special(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    other = Property(name="Second hotel", time_zone="Europe/Oslo")
    db_session.add(other)
    await db_session.flush()
    fake_admin = Role(property_id=other.id, name=ADMIN_ROLE_NAME, is_system=False)
    db_session.add(fake_admin)
    await db_session.flush()
    user = await make_user("pretender", role=None)
    db_session.add(UserPropertyRole(user_id=user.id, property_id=other.id, role_id=fake_admin.id))
    await db_session.flush()

    assert await get_effective_permissions(db_session, user.id, other.id) == frozenset()
