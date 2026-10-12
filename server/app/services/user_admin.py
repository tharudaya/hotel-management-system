"""User administration: list, view, create, edit, role assignment, (de)activation.

All rules live here, not in the routers:

- You can only manage users whose permissions are a subset of yours, and only assign
  roles whose permissions are a subset of yours (no privilege escalation).
- You cannot deactivate yourself, change your own role, or expire yourself.
- A property always keeps at least one active Admin (not deactivated, not expired,
  not merely invited). Changes that could remove the last one take a row lock on the
  property's Admin role, so two concurrent changes cannot both pass the check.
- Deactivation and role changes revoke the user's sessions; their WebSockets close.
- Usernames and emails are unique, case-insensitive (stored lowercase).
- One audit_log row per change. Responses never include password hashes or sessions.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    ColumnElement,
    and_,
    func,
    not_,
    or_,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import QueryableAttribute

from app.core.permissions import ADMIN_ROLE_NAME, ALL_PERMISSION_KEYS
from app.models import AuditAction, Property, Role, RolePermission, User, UserPropertyRole
from app.realtime.bus import EventBus
from app.schemas.admin_users import (
    PropertyRole,
    SessionsRevoked,
    UserCreate,
    UserDetail,
    UserListItem,
    UserListPage,
    UserListQuery,
    UserStatus,
    UserUpdate,
)
from app.schemas.auth import PropertyRef, RoleRef
from app.services.audit import ClientInfo, record_change
from app.services.auth import publish_sessions_ended, revoke_user_sessions
from app.services.errors import DomainError, NotFoundError, PermissionDeniedError
from app.services.permissions import publish_permissions_changed, user_can_act

PROFILE_FIELDS: tuple[str, ...] = (
    "full_name",
    "email",
    "phone",
    "job_title",
    "employee_id",
    "expires_at",
)


def user_status(user: User, now: datetime) -> UserStatus:
    if user.deactivated_at is not None:
        return UserStatus.DEACTIVATED
    if user.expires_at is not None and user.expires_at <= now:
        return UserStatus.EXPIRED
    if user.password_hash is None:
        return UserStatus.INVITED
    return UserStatus.ACTIVE


def status_condition(status: UserStatus, now: datetime) -> ColumnElement[bool]:
    """SQL equivalent of user_status() == status."""
    not_expired = or_(User.expires_at.is_(None), User.expires_at > now)
    match status:
        case UserStatus.DEACTIVATED:
            return User.deactivated_at.is_not(None)
        case UserStatus.EXPIRED:
            return and_(User.deactivated_at.is_(None), not_(not_expired))
        case UserStatus.INVITED:
            return and_(User.deactivated_at.is_(None), not_expired, User.password_hash.is_(None))
        case UserStatus.ACTIVE:
            return and_(User.deactivated_at.is_(None), not_expired, User.password_hash.is_not(None))


SORT_COLUMNS: dict[str, ColumnElement[Any] | QueryableAttribute[Any]] = {
    "name": func.lower(User.full_name),
    "username": User.username,
    "email": User.email,
    "created_at": User.created_at,
    "last_login_at": User.last_login_at,
}


def _jsonable(value: object) -> object:
    return value.isoformat() if isinstance(value, datetime) else value


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass
class UserAdminService:
    """Acts on behalf of `actor` at one property. Construct per request."""

    db: AsyncSession
    bus: EventBus
    actor: User
    actor_permissions: frozenset[str]
    property_id: int
    client: ClientInfo

    # --- reads -----------------------------------------------------------------------

    async def list_users(self, query: UserListQuery) -> UserListPage:
        now = datetime.now(UTC)
        base = (
            select(User, Role)
            .outerjoin(
                UserPropertyRole,
                and_(
                    UserPropertyRole.user_id == User.id,
                    UserPropertyRole.property_id == self.property_id,
                ),
            )
            .outerjoin(Role, Role.id == UserPropertyRole.role_id)
        )
        if query.q:
            pattern = f"%{_escape_like(query.q.strip().lower())}%"
            base = base.where(
                or_(
                    func.lower(User.full_name).like(pattern, escape="\\"),
                    User.username.like(pattern, escape="\\"),
                    User.email.like(pattern, escape="\\"),
                    func.lower(User.employee_id).like(pattern, escape="\\"),
                )
            )
        if query.role_id is not None:
            base = base.where(UserPropertyRole.role_id == query.role_id)
        if query.status is not None:
            base = base.where(status_condition(query.status, now))

        total = await self.db.scalar(select(func.count()).select_from(base.subquery())) or 0

        column = SORT_COLUMNS[query.sort.lstrip("-")]
        order = column.desc() if query.sort.startswith("-") else column.asc()
        rows = (
            await self.db.execute(
                base.order_by(order.nulls_last(), User.id)
                .offset((query.page - 1) * query.page_size)
                .limit(query.page_size)
            )
        ).all()

        return UserListPage(
            items=[
                UserListItem(
                    id=user.id,
                    username=user.username,
                    full_name=user.full_name,
                    email=user.email,
                    job_title=user.job_title,
                    employee_id=user.employee_id,
                    status=user_status(user, now),
                    role=RoleRef(id=role.id, name=role.name) if role else None,
                    last_login_at=user.last_login_at,
                    expires_at=user.expires_at,
                    created_at=user.created_at,
                )
                for user, role in rows
            ],
            total=total,
            page=query.page,
            page_size=query.page_size,
        )

    async def get_user(self, user_id: int) -> UserDetail:
        user = await self._load(user_id)
        return await self._detail(user)

    # --- writes ----------------------------------------------------------------------

    async def create_user(self, data: UserCreate) -> UserDetail:
        username = data.username.lower()
        email = data.email.lower() if data.email else None
        await self._ensure_unique(username=username, email=email)
        user = User(
            username=username,
            full_name=data.full_name,
            email=email,
            phone=data.phone,
            job_title=data.job_title,
            employee_id=data.employee_id,
            expires_at=data.expires_at,
            password_hash=None,  # invited: no password until they set one
        )
        self.db.add(user)
        await self._flush_unique()
        after = {"username": username, **{f: _jsonable(getattr(user, f)) for f in PROFILE_FIELDS}}
        self._audit(AuditAction.USER_CREATED, user, before=None, after=after)
        await self.db.commit()
        return await self._detail(user)

    async def update_user(self, user_id: int, data: UserUpdate) -> UserDetail:
        target = await self._load(user_id)
        await self._ensure_can_manage(target)
        changes: dict[str, object] = {}
        for field in data.model_fields_set:
            value = getattr(data, field)
            if field == "email" and value is not None:
                value = value.lower()
            if getattr(target, field) != value:
                changes[field] = value
        if not changes:
            return await self._detail(target)

        now = datetime.now(UTC)
        if "email" in changes and changes["email"] is not None:
            await self._ensure_unique(email=str(changes["email"]), exclude_user_id=target.id)
        if "expires_at" in changes and changes["expires_at"] is not None:
            expires_at = changes["expires_at"]
            if (
                target.id == self.actor.id
                and isinstance(expires_at, datetime)
                and expires_at <= now
            ):
                raise DomainError("You cannot expire your own account.", code="self_action")
            await self._ensure_not_last_admin(
                target, "cannot be given an expiry date while they are the only active admin"
            )

        before = {f: _jsonable(getattr(target, f)) for f in changes}
        for field, value in changes.items():
            setattr(target, field, value)
        await self._flush_unique()
        after = {f: _jsonable(getattr(target, f)) for f in changes}
        self._audit(AuditAction.USER_UPDATED, target, before=before, after=after)
        await self.db.commit()
        if "expires_at" in changes:
            # Expiry affects access; let live connections re-check.
            await publish_permissions_changed(self.bus, self.property_id, [target.id])
        return await self._detail(target)

    async def assign_role(self, user_id: int, role_id: int) -> UserDetail:
        target = await self._load(user_id)
        if target.id == self.actor.id:
            raise DomainError("You cannot change your own role.", code="self_action")
        new_role = await self.db.scalar(
            select(Role).where(Role.id == role_id, Role.property_id == self.property_id)
        )
        if new_role is None:
            raise NotFoundError(f"Role {role_id} not found at this property")
        await self._ensure_can_manage(target)
        if not await self._role_permissions(new_role) <= self.actor_permissions:
            raise PermissionDeniedError(
                f"You cannot assign the role {new_role.name!r}: it has permissions you don't have."
            )

        current = await self._assignment(target.id)
        if current is not None and current.role_id == new_role.id:
            return await self._detail(target)  # nothing to change
        if not self._is_admin_role(new_role):
            await self._ensure_not_last_admin(target, "cannot lose the Admin role")

        before_role = await self.db.get(Role, current.role_id) if current else None
        now = datetime.now(UTC)
        if current is None:
            self.db.add(
                UserPropertyRole(
                    user_id=target.id,
                    property_id=self.property_id,
                    role_id=new_role.id,
                    assigned_by=self.actor.id,
                    assigned_at=now,
                )
            )
        else:
            current.role_id = new_role.id
            current.assigned_by = self.actor.id
            current.assigned_at = now
        revoked = await revoke_user_sessions(self.db, target.id, now)
        self._audit(
            AuditAction.USER_ROLE_CHANGED,
            target,
            before={
                "role_id": before_role.id if before_role else None,
                "role_name": before_role.name if before_role else None,
            },
            after={
                "role_id": new_role.id,
                "role_name": new_role.name,
                "sessions_revoked": len(revoked),
            },
        )
        await self.db.commit()
        await publish_sessions_ended(self.bus, target.id, revoked)
        await publish_permissions_changed(self.bus, self.property_id, [target.id])
        return await self._detail(target)

    async def deactivate(self, user_id: int) -> UserDetail:
        target = await self._load(user_id)
        if target.id == self.actor.id:
            raise DomainError("You cannot deactivate yourself.", code="self_action")
        await self._ensure_can_manage(target)
        if target.deactivated_at is not None:
            raise DomainError(
                f"{target.username} is already deactivated.", code="invalid_transition"
            )
        await self._ensure_not_last_admin(target, "cannot be deactivated")

        now = datetime.now(UTC)
        target.deactivated_at = now
        target.deactivated_by = self.actor.id
        revoked = await revoke_user_sessions(self.db, target.id, now)
        self._audit(
            AuditAction.USER_DEACTIVATED,
            target,
            before={"deactivated_at": None},
            after={"deactivated_at": now.isoformat(), "sessions_revoked": len(revoked)},
        )
        await self.db.commit()
        await publish_sessions_ended(self.bus, target.id, revoked)
        await publish_permissions_changed(self.bus, self.property_id, [target.id])
        return await self._detail(target)

    async def reactivate(self, user_id: int) -> UserDetail:
        target = await self._load(user_id)
        await self._ensure_can_manage(target)
        if target.deactivated_at is None:
            raise DomainError(f"{target.username} is not deactivated.", code="invalid_transition")
        before = {"deactivated_at": target.deactivated_at.isoformat()}
        target.deactivated_at = None
        target.deactivated_by = None
        self._audit(
            AuditAction.USER_REACTIVATED, target, before=before, after={"deactivated_at": None}
        )
        await self.db.commit()
        return await self._detail(target)

    async def revoke_sessions(self, user_id: int) -> SessionsRevoked:
        target = await self._load(user_id)
        await self._ensure_can_manage(target)
        revoked = await revoke_user_sessions(self.db, target.id, datetime.now(UTC))
        self._audit(
            AuditAction.USER_SESSIONS_REVOKED,
            target,
            before=None,
            after={"sessions_revoked": len(revoked)},
        )
        await self.db.commit()
        await publish_sessions_ended(self.bus, target.id, revoked)
        return SessionsRevoked(revoked_sessions=len(revoked))

    # --- guards ----------------------------------------------------------------------

    async def _ensure_can_manage(self, target: User) -> None:
        """Only users whose permissions are a subset of yours (no managing your betters)."""
        role = await self._role_of(target.id)
        target_permissions = await self._role_permissions(role) if role else frozenset()
        if not target_permissions <= self.actor_permissions:
            raise PermissionDeniedError(
                f"You cannot manage {target.username}: they have permissions you don't have."
            )

    async def _ensure_not_last_admin(self, target: User, what: str) -> None:
        """Refuse if `target` is this property's last active Admin.

        Locks the property's Admin role row first, so concurrent admin changes are
        serialised and cannot both see "another admin remains".
        """
        admin_role_id = await self.db.scalar(
            select(Role.id)
            .where(
                Role.property_id == self.property_id,
                Role.is_system,
                Role.name == ADMIN_ROLE_NAME,
            )
            .with_for_update()
        )
        if admin_role_id is None:
            return
        now = datetime.now(UTC)
        active_admins = list(
            (
                await self.db.scalars(
                    select(User.id)
                    .join(UserPropertyRole, UserPropertyRole.user_id == User.id)
                    .where(
                        UserPropertyRole.property_id == self.property_id,
                        UserPropertyRole.role_id == admin_role_id,
                        user_can_act(now),
                        User.password_hash.is_not(None),
                    )
                )
            ).all()
        )
        if active_admins == [target.id]:
            raise DomainError(
                f"{target.username} is the last active Admin of this property and {what}. "
                "Give another user the Admin role first.",
                code="last_admin",
            )

    async def _ensure_unique(
        self, *, username: str | None = None, email: str | None = None, exclude_user_id: int = 0
    ) -> None:
        if username is not None:
            taken = await self.db.scalar(
                select(User.id).where(User.username == username, User.id != exclude_user_id)
            )
            if taken is not None:
                raise DomainError(
                    f"The username {username!r} is already taken.", code="username_taken"
                )
        if email is not None:
            taken = await self.db.scalar(
                select(User.id).where(User.email == email, User.id != exclude_user_id)
            )
            if taken is not None:
                raise DomainError(f"The email {email!r} is already in use.", code="email_taken")

    async def _flush_unique(self) -> None:
        """Flush, turning a unique-constraint race into the same 409 as the pre-check."""
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            text = str(exc.orig)
            if "uq_users_email" in text:
                raise DomainError("That email is already in use.", code="email_taken") from None
            if "uq_users_username" in text:
                raise DomainError(
                    "That username is already taken.", code="username_taken"
                ) from None
            raise

    # --- helpers ---------------------------------------------------------------------

    async def _load(self, user_id: int) -> User:
        user = await self.db.scalar(
            select(User).where(User.id == user_id).execution_options(populate_existing=True)
        )
        if user is None:
            raise NotFoundError(f"User {user_id} not found")
        return user

    async def _assignment(self, user_id: int) -> UserPropertyRole | None:
        return await self.db.scalar(
            select(UserPropertyRole)
            .where(
                UserPropertyRole.user_id == user_id,
                UserPropertyRole.property_id == self.property_id,
            )
            .with_for_update()
        )

    async def _role_of(self, user_id: int) -> Role | None:
        return await self.db.scalar(
            select(Role)
            .join(UserPropertyRole, UserPropertyRole.role_id == Role.id)
            .where(
                UserPropertyRole.user_id == user_id,
                UserPropertyRole.property_id == self.property_id,
            )
        )

    @staticmethod
    def _is_admin_role(role: Role) -> bool:
        return role.is_system and role.name == ADMIN_ROLE_NAME

    async def _role_permissions(self, role: Role) -> frozenset[str]:
        """What the role grants, regardless of whether its holder is active."""
        if self._is_admin_role(role):
            return ALL_PERMISSION_KEYS
        keys = await self.db.scalars(
            select(RolePermission.permission_key).where(RolePermission.role_id == role.id)
        )
        return frozenset(keys.all()) & ALL_PERMISSION_KEYS

    async def _detail(self, user: User) -> UserDetail:
        rows = (
            await self.db.execute(
                select(Property, Role)
                .join(UserPropertyRole, UserPropertyRole.property_id == Property.id)
                .join(Role, Role.id == UserPropertyRole.role_id)
                .where(UserPropertyRole.user_id == user.id)
                .order_by(Property.id)
            )
        ).all()
        await self.db.refresh(user)
        return UserDetail(
            id=user.id,
            username=user.username,
            full_name=user.full_name,
            email=user.email,
            phone=user.phone,
            job_title=user.job_title,
            employee_id=user.employee_id,
            status=user_status(user, datetime.now(UTC)),
            roles=[
                PropertyRole(
                    property=PropertyRef(id=prop.id, name=prop.name, time_zone=prop.time_zone),
                    role=RoleRef(id=role.id, name=role.name),
                )
                for prop, role in rows
            ],
            created_at=user.created_at,
            last_login_at=user.last_login_at,
            expires_at=user.expires_at,
            deactivated_at=user.deactivated_at,
        )

    def _audit(
        self,
        action: AuditAction,
        target: User,
        *,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        record_change(
            self.db,
            action=action,
            target_type="user",
            target_id=target.id,
            actor=self.actor,
            property_id=self.property_id,
            client=self.client,
            before=before,
            after=after,
        )
