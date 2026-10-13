"""Creating the first Admin of a fresh install (used by app/scripts/create_admin.py).

Allowed only while the property has no *active* Admin (not deactivated, not expired,
not merely invited): on a fresh install, or to recover when every admin is locked
out. Otherwise admins are created in the app, where the guard rules apply.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.passwords import PasswordPolicyError, check_new_password
from app.core.permissions import ADMIN_ROLE_NAME
from app.core.security import hash_password
from app.models import AuditAction, Role, User, UserPropertyRole
from app.services.audit import record_change
from app.services.errors import DomainError, InvalidInputError
from app.services.permissions import get_default_property_id, user_can_act


async def create_first_admin(
    db: AsyncSession, *, username: str, full_name: str, email: str | None, password: str
) -> User:
    username = username.strip().lower()
    property_id = await get_default_property_id(db)
    admin_role = await db.scalar(
        select(Role)
        .where(Role.property_id == property_id, Role.is_system, Role.name == ADMIN_ROLE_NAME)
        .with_for_update()  # serialise with other admin changes (see user_admin)
    )
    if admin_role is None:
        raise DomainError("The Admin role is missing; run the database migrations.")

    active_admin = await db.scalar(
        select(User.username)
        .join(UserPropertyRole, UserPropertyRole.user_id == User.id)
        .where(
            UserPropertyRole.property_id == property_id,
            UserPropertyRole.role_id == admin_role.id,
            user_can_act(datetime.now(UTC)),
            User.password_hash.is_not(None),
        )
        .limit(1)
    )
    if active_admin is not None:
        raise DomainError(
            f"An active admin already exists ({active_admin}). Create further admins in "
            "the app (Admin > Users).",
            code="admin_exists",
        )
    if await db.scalar(select(User.id).where(User.username == username)) is not None:
        raise DomainError(f"The username {username!r} is already taken.", code="username_taken")
    try:
        check_new_password(password, username=username)
    except PasswordPolicyError as exc:
        raise InvalidInputError(exc.message, code=exc.code) from None

    user = User(
        username=username,
        full_name=full_name.strip(),
        email=email.strip().lower() if email else None,
        password_hash=hash_password(password),
    )
    db.add(user)
    await db.flush()
    db.add(
        UserPropertyRole(
            user_id=user.id,
            property_id=property_id,
            role_id=admin_role.id,
            assigned_at=datetime.now(UTC),
        )
    )
    record_change(
        db,
        action=AuditAction.USER_CREATED,
        target_type="user",
        target_id=user.id,
        actor=None,  # the system (command line)
        property_id=property_id,
        client=None,
        before=None,
        after={
            "username": username,
            "full_name": user.full_name,
            "role_id": admin_role.id,
            "role_name": admin_role.name,
            "created_by": "create_admin command",
        },
    )
    await db.commit()
    return user
