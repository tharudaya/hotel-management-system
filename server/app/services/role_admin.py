"""Role administration: list, view, create, edit and delete roles at one property.

All rules live here, not in the routers:

- The Admin system role cannot be changed at all; other system roles cannot be
  renamed or deleted, but their description and permissions can be edited.
- You can only add permissions you hold yourself, and you can only edit or delete
  roles whose permissions are all within yours (no managing a more powerful role).
- Unknown permission keys are rejected (422, with the list).
- A role can only be deleted while no user has it.
- Role names are unique per property, case-insensitive.
- Changing a role's permissions publishes permissions_changed for its holders, so
  their live connections re-check access and their clients refetch what they may see.
- One audit_log row per change; permission changes record the added and removed keys.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import ADMIN_ROLE_NAME, ALL_PERMISSION_KEYS, PERMISSIONS, P
from app.models import AuditAction, Role, RolePermission, User, UserPropertyRole
from app.realtime.bus import EventBus
from app.schemas.admin_roles import (
    CatalogPermission,
    PermissionGroup,
    RoleCreate,
    RoleDetail,
    RoleSummary,
    RoleUpdate,
    RoleUser,
)
from app.services.audit import ClientInfo, record_change
from app.services.errors import (
    DomainError,
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
)
from app.services.permissions import publish_permissions_changed
from app.services.user_admin import user_status


def permission_catalog() -> list[PermissionGroup]:
    """The code catalog grouped by area, groups and keys in catalog order."""
    groups: dict[str, list[CatalogPermission]] = {}
    for permission in PERMISSIONS:
        groups.setdefault(permission.group, []).append(
            CatalogPermission(key=permission.key, description=permission.description)
        )
    return [PermissionGroup(group=group, permissions=items) for group, items in groups.items()]


def is_admin_role(role: Role) -> bool:
    return role.is_system and role.name == ADMIN_ROLE_NAME


@dataclass
class RoleAdminService:
    """Acts on behalf of `actor` at one property. Construct per request."""

    db: AsyncSession
    bus: EventBus
    actor: User
    actor_permissions: frozenset[str]
    property_id: int
    client: ClientInfo

    # --- reads -----------------------------------------------------------------------

    async def list_roles(self) -> list[RoleSummary]:
        roles = list(
            (
                await self.db.scalars(
                    select(Role)
                    .where(Role.property_id == self.property_id)
                    .order_by(Role.is_system.desc(), Role.id)
                )
            ).all()
        )
        role_ids = [role.id for role in roles]
        keys: dict[int, set[str]] = defaultdict(set)
        for role_id, key in (
            await self.db.execute(
                select(RolePermission.role_id, RolePermission.permission_key).where(
                    RolePermission.role_id.in_(role_ids)
                )
            )
        ).all():
            keys[role_id].add(key)
        counts: dict[int, int] = {
            role_id: count
            for role_id, count in (
                await self.db.execute(
                    select(UserPropertyRole.role_id, func.count())
                    .where(UserPropertyRole.role_id.in_(role_ids))
                    .group_by(UserPropertyRole.role_id)
                )
            ).all()
        }
        return [
            self._summary(role, self._effective(role, keys[role.id]), counts.get(role.id, 0))
            for role in roles
        ]

    async def get_role(self, role_id: int) -> RoleDetail:
        return await self._detail(await self._load(role_id))

    # --- writes ----------------------------------------------------------------------

    async def create_role(self, data: RoleCreate) -> RoleDetail:
        requested = self._known(data.permissions)
        description = data.description or None
        copied_from: Role | None = None
        if data.copy_from_role_id is not None:
            copied_from = await self._load(data.copy_from_role_id)
            requested |= await self._permissions_of(copied_from)
            if description is None:
                description = copied_from.description
        self._ensure_can_grant(requested)
        await self._ensure_name_free(data.name)

        role = Role(
            property_id=self.property_id,
            name=data.name,
            description=description,
            is_system=False,
        )
        self.db.add(role)
        await self._flush_unique()
        self.db.add_all(RolePermission(role_id=role.id, permission_key=k) for k in requested)
        after: dict[str, Any] = {
            "name": role.name,
            "description": role.description,
            "permissions": {"added": sorted(requested), "removed": []},
        }
        if copied_from is not None:
            after["copied_from"] = {"id": copied_from.id, "name": copied_from.name}
        self._audit(AuditAction.ROLE_CREATED, role, before=None, after=after)
        await self.db.commit()
        # Nobody has the new role yet: no permissions_changed needed.
        return await self._detail(role)

    async def update_role(self, role_id: int, data: RoleUpdate) -> RoleDetail:
        role = await self._load(role_id, lock=True)
        if is_admin_role(role):
            raise DomainError(
                "The Admin role always has every permission and cannot be changed.",
                code="role_immutable",
            )
        current = await self._permissions_of(role)
        self._ensure_can_manage(role, current)
        sent = data.model_fields_set

        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        if "name" in sent:
            if data.name is None:
                raise InvalidInputError("A role needs a name.", code="name_required")
            if data.name != role.name:
                if role.is_system:
                    raise DomainError(
                        f"{role.name!r} is a system role and cannot be renamed.",
                        code="system_role",
                    )
                await self._ensure_name_free(data.name, exclude_role_id=role.id)
                before["name"], after["name"] = role.name, data.name
                role.name = data.name
        if "description" in sent:
            description = data.description or None
            if description != role.description:
                before["description"], after["description"] = role.description, description
                role.description = description

        added: set[str] = set()
        removed: set[str] = set()
        if "permissions" in sent:
            if data.permissions is None:
                raise InvalidInputError(
                    "Send a list of permission keys (it can be empty).", code="invalid_input"
                )
            wanted = self._known(data.permissions)
            added, removed = wanted - current, set(current - wanted)
            self._ensure_can_grant(added)
            if removed:
                await self.db.execute(
                    delete(RolePermission).where(
                        RolePermission.role_id == role.id,
                        RolePermission.permission_key.in_(removed),
                    )
                )
            self.db.add_all(RolePermission(role_id=role.id, permission_key=k) for k in added)
            if added or removed:
                after["permissions"] = {"added": sorted(added), "removed": sorted(removed)}

        if not after:
            return await self._detail(role)  # nothing changed
        role.updated_at = datetime.now(UTC)
        await self._flush_unique()
        self._audit(AuditAction.ROLE_UPDATED, role, before=before or None, after=after)
        holders = await self._holder_ids(role.id)
        await self.db.commit()
        if added or removed:
            # Requests already re-read permissions every time; this makes live
            # connections re-check access and tells the clients to refetch.
            await publish_permissions_changed(self.bus, self.property_id, holders)
        return await self._detail(role)

    async def delete_role(self, role_id: int) -> None:
        role = await self._load(role_id, lock=True)
        if role.is_system:
            raise DomainError(
                f"{role.name!r} is a system role and cannot be deleted.", code="system_role"
            )
        current = await self._permissions_of(role)
        self._ensure_can_manage(role, current)
        holders = await self._holder_ids(role.id)
        if holders:
            raise DomainError(
                f"{len(holders)} user(s) still have the role {role.name!r}. "
                "Give them another role first.",
                code="role_in_use",
            )
        self._audit(
            AuditAction.ROLE_DELETED,
            role,
            before={
                "name": role.name,
                "description": role.description,
                "permissions": {"added": [], "removed": sorted(current)},
            },
            after=None,
        )
        try:
            await self.db.delete(role)
            await self.db.commit()
        except IntegrityError:
            # Someone was given the role in the meantime (the FK is RESTRICT).
            await self.db.rollback()
            raise DomainError(
                "Someone was just given this role. Give them another role first.",
                code="role_in_use",
            ) from None

    # --- guards ----------------------------------------------------------------------

    @staticmethod
    def _known(keys: list[str]) -> set[str]:
        unknown = sorted(set(keys) - ALL_PERMISSION_KEYS)
        if unknown:
            raise InvalidInputError(
                f"Unknown permission(s): {', '.join(unknown)}.", code="unknown_permission"
            )
        return set(keys)

    def _ensure_can_grant(self, keys: set[str]) -> None:
        missing = sorted(keys - self.actor_permissions)
        if missing:
            raise PermissionDeniedError(
                f"You cannot grant permissions you don't have: {', '.join(missing)}.",
                code="permission_not_held",
            )

    def _ensure_can_manage(self, role: Role, permissions: frozenset[str]) -> None:
        if not permissions <= self.actor_permissions:
            raise PermissionDeniedError(
                f"You cannot change the role {role.name!r}: it has permissions you don't have.",
                code="role_above_you",
            )

    async def _ensure_name_free(self, name: str, *, exclude_role_id: int = 0) -> None:
        taken = await self.db.scalar(
            select(Role.id).where(
                Role.property_id == self.property_id,
                func.lower(Role.name) == name.lower(),
                Role.id != exclude_role_id,
            )
        )
        if taken is not None:
            raise DomainError(f"A role named {name!r} already exists.", code="role_name_taken")

    async def _flush_unique(self) -> None:
        """Flush, turning a name race into the same 409 as the pre-check."""
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            if "uq_roles_property_id_name" in str(exc.orig):
                raise DomainError(
                    "A role with that name already exists.", code="role_name_taken"
                ) from None
            raise

    # --- helpers ---------------------------------------------------------------------

    async def _load(self, role_id: int, *, lock: bool = False) -> Role:
        query = (
            select(Role)
            .where(Role.id == role_id, Role.property_id == self.property_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            # Serialises concurrent edits of one role (and delete vs. edit).
            query = query.with_for_update()
        role = await self.db.scalar(query)
        if role is None:
            raise NotFoundError(f"Role {role_id} not found")
        return role

    @staticmethod
    def _effective(role: Role, stored: set[str]) -> frozenset[str]:
        """What the role grants: Admin always everything; stale keys never count."""
        return (
            ALL_PERMISSION_KEYS if is_admin_role(role) else frozenset(stored) & ALL_PERMISSION_KEYS
        )

    async def _permissions_of(self, role: Role) -> frozenset[str]:
        keys = await self.db.scalars(
            select(RolePermission.permission_key).where(RolePermission.role_id == role.id)
        )
        return self._effective(role, set(keys.all()))

    async def _holder_ids(self, role_id: int) -> list[int]:
        ids = await self.db.scalars(
            select(UserPropertyRole.user_id).where(UserPropertyRole.role_id == role_id)
        )
        return sorted(ids.all())

    def _summary(self, role: Role, permissions: frozenset[str], user_count: int) -> RoleSummary:
        can_edit = (
            P.ROLE_MANAGE in self.actor_permissions
            and not is_admin_role(role)
            and permissions <= self.actor_permissions
        )
        return RoleSummary(
            id=role.id,
            name=role.name,
            description=role.description,
            is_system=role.is_system,
            is_admin=is_admin_role(role),
            user_count=user_count,
            permission_count=len(permissions),
            can_edit=can_edit,
            can_delete=can_edit and not role.is_system and user_count == 0,
        )

    async def _detail(self, role: Role) -> RoleDetail:
        await self.db.refresh(role)
        permissions = await self._permissions_of(role)
        users = (
            await self.db.scalars(
                select(User)
                .join(UserPropertyRole, UserPropertyRole.user_id == User.id)
                .where(UserPropertyRole.role_id == role.id)
                .order_by(func.lower(User.full_name), User.id)
            )
        ).all()
        now = datetime.now(UTC)
        summary = self._summary(role, permissions, len(users))
        return RoleDetail(
            **summary.model_dump(),
            permissions=sorted(permissions),
            users=[
                RoleUser(
                    id=user.id,
                    username=user.username,
                    full_name=user.full_name,
                    status=user_status(user, now),
                )
                for user in users
            ],
        )

    def _audit(
        self,
        action: AuditAction,
        role: Role,
        *,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        record_change(
            self.db,
            action=action,
            target_type="role",
            target_id=role.id,
            actor=self.actor,
            property_id=self.property_id,
            client=self.client,
            before=before,
            after=after,
        )
