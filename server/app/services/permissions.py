"""Permission catalog sync, role lookup and effective permissions."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, and_, delete, or_, select, true
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import (
    ADMIN_ROLE_NAME,
    ALL_PERMISSION_KEYS,
    PERMISSIONS,
)
from app.core.permissions import Permission as CatalogPermission
from app.models import Permission, Property, Role, RolePermission, User, UserPropertyRole
from app.schemas.auth import RoleRef, UserOut


def user_can_act(now: datetime | ColumnElement[datetime]) -> ColumnElement[bool]:
    """SQL condition: the user is not deactivated and not past their expiry date."""
    return and_(
        User.deactivated_at.is_(None),
        or_(User.expires_at.is_(None), User.expires_at > now),
    )


@dataclass(frozen=True)
class SyncResult:
    added: frozenset[str]
    removed: frozenset[str]


async def sync_permission_catalog(
    db: AsyncSession, catalog: Iterable[CatalogPermission] = PERMISSIONS
) -> SyncResult:
    """Make the permissions table match the code catalog, and commit.

    New keys are inserted and changed descriptions/groups updated; keys no longer in
    the catalog are deleted (their role_permissions rows go with them, ON DELETE
    CASCADE). Admin system roles are then granted every key, so Admin keeps "all
    permissions" as the catalog grows. Idempotent and safe to run on every startup.
    """
    entries = {p.key: p for p in catalog}
    existing = frozenset((await db.scalars(select(Permission.key))).all())

    if entries:
        insert = pg_insert(Permission).values(
            [
                {"key": p.key, "description": p.description, "group": p.group}
                for p in entries.values()
            ]
        )
        await db.execute(
            insert.on_conflict_do_update(
                index_elements=[Permission.key],
                set_={"description": insert.excluded.description, "group": insert.excluded.group},
                # Only touch rows that actually changed.
                where=or_(
                    Permission.description != insert.excluded.description,
                    Permission.group != insert.excluded.group,
                ),
            )
        )

    removed = existing - entries.keys()
    if removed:
        await db.execute(delete(Permission).where(Permission.key.in_(removed)))

    admin_grants = (
        select(Role.id, Permission.key)
        .join(Permission, true())
        .where(Role.is_system, Role.name == ADMIN_ROLE_NAME)
    )
    await db.execute(
        pg_insert(RolePermission)
        .from_select(["role_id", "permission_key"], admin_grants)
        .on_conflict_do_nothing()
    )
    await db.commit()
    return SyncResult(added=frozenset(entries.keys() - existing), removed=removed)


async def get_default_property_id(db: AsyncSession) -> int:
    """The property the API works with until rooms and reservations are property-scoped.

    Single-property installations have exactly one (created by the migration).
    """
    property_id = await db.scalar(select(Property.id).order_by(Property.id).limit(1))
    if property_id is None:
        raise RuntimeError("No property exists; run the database migrations.")
    return property_id


async def get_assigned_role(db: AsyncSession, user_id: int, property_id: int) -> Role | None:
    return await db.scalar(
        select(Role)
        .join(UserPropertyRole, UserPropertyRole.role_id == Role.id)
        .where(UserPropertyRole.user_id == user_id, UserPropertyRole.property_id == property_id)
    )


async def get_effective_permissions(
    db: AsyncSession, user_id: int, property_id: int, *, now: datetime | None = None
) -> frozenset[str]:
    """Permission keys the user holds at the property right now.

    Empty for deactivated or expired users and for users without a role there. The
    Admin system role always yields the whole catalog. Keys that are not (or no longer)
    in the code catalog are never returned.
    """
    role = (
        await db.execute(
            select(Role.id, Role.name, Role.is_system)
            .join(UserPropertyRole, UserPropertyRole.role_id == Role.id)
            .join(User, User.id == UserPropertyRole.user_id)
            .where(
                UserPropertyRole.user_id == user_id,
                UserPropertyRole.property_id == property_id,
                user_can_act(now or datetime.now(UTC)),
            )
        )
    ).one_or_none()
    if role is None:
        return frozenset()
    if role.is_system and role.name == ADMIN_ROLE_NAME:
        return ALL_PERMISSION_KEYS
    keys = await db.scalars(
        select(RolePermission.permission_key).where(RolePermission.role_id == role.id)
    )
    return frozenset(keys.all()) & ALL_PERMISSION_KEYS


async def describe_user(db: AsyncSession, user: User, property_id: int) -> UserOut:
    """The user with their role and effective permissions at the property."""
    role = await get_assigned_role(db, user.id, property_id)
    permissions = await get_effective_permissions(db, user.id, property_id)
    return UserOut(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=RoleRef(id=role.id, name=role.name) if role else None,
        permissions=sorted(permissions),
    )
